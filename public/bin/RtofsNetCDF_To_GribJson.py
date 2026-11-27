from datetime import datetime, timedelta, timezone
import argparse
import gc
import json
from pathlib import Path
import pickle
import re
import sqlite3
import time
from typing import Dict, List, Optional, Tuple
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay
from shapely.geometry import Point
from shapely.ops import polygonize
from shapely.validation import make_valid
import topojson as tp
import xarray as xr

DEFAULT_TOPO_PATH = "/Library/WebServer/Documents/data/earth-topo.json"
DEFAULT_LAND_BUFFER_METERS = -1000.0
DEFAULT_DOWNLOAD_DIR = "/Library/WebServer/Documents/data/download"
DEFAULT_OUTPUT_DIR = "/Library/WebServer/Documents/data/rtofs"
DEFAULT_ICE_GRID_PATH = "/Library/WebServer/Documents/data/rtofs/shared_grid_ice.pkl"
DEFAULT_PROG_GRID_PATH = "/Library/WebServer/Documents/data/rtofs/shared_grid_prog.pkl"
DEFAULT_DB_PATH = "/Users/unchil/AndroidStudioProjects/OceanWaterInfo/oceanwater.sqlite"

POLYGON_LAYERS: Tuple[str, ...] = (
    "countries1",
    "land1",
    "countries2",
    "land2",
    "land3",
    "land4",
)
FALLBACK_COAST_LAYER = "coastline_50m"

TARGET_LONS = np.arange(0, 360, 1.0)
TARGET_LATS = np.arange(90, -91, -1.0)
GRID_LON, GRID_LAT = np.meshgrid(TARGET_LONS, TARGET_LATS)
NY, NX = GRID_LON.shape

# (label, lat, lon, expect_none_in_prog)
VERIFICATION_COORDINATES: List[Tuple[str, float, float, bool]] = [
    ("서울 (내륙)", 37.5665, 126.9780, True),
    ("안양 (내륙)", 37.3943, 126.9568, True),
    ("중앙아시아 (대륙 내륙)", 45.0000, 90.0000, True),
    ("북미 중부 (대륙 내륙)", 40.0000, -100.0000, True),
    ("동해 (해상)", 36.5000, 131.0000, False),
    ("북태평양 (해상)", 35.0000, -150.0000, False),
    ("남대서양 (해상)", -25.0000, -15.0000, False),
]

ICE_VARIABLE_SPECS: List[Dict] = [
    {
        "name": "ice_coverage",
        "output_type": "ice_coverage",
        "has_layer": False,
        "parameterCategory": 2,
        "parameterCategoryName": "Ice",
        "parameterNumber": 0,
        "parameterNumberName": "Ice_cover",
        "parameterUnit": "1",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
    },
    {
        "name": "ice_temperature",
        "output_type": "ice_temperature",
        "has_layer": False,
        "parameterCategory": 2,
        "parameterCategoryName": "Ice",
        "parameterNumber": 2,
        "parameterNumberName": "Ice_surface_temperature",
        "parameterUnit": "degC",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
    },
    {
        "name": "ice_thickness",
        "output_type": "ice_thickness",
        "has_layer": False,
        "parameterCategory": 2,
        "parameterCategoryName": "Ice",
        "parameterNumber": 1,
        "parameterNumberName": "Ice_thickness",
        "parameterUnit": "m",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
    },
    {
        "name": "ice_uvelocity",
        "output_type": "ice_currents",
        "has_layer": False,
        "parameterCategory": 2,
        "parameterCategoryName": "Ice",
        "parameterNumber": 3,
        "parameterNumberName": "Ice_drift_u_velocity",
        "parameterUnit": "m.s-1",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
    },
    {
        "name": "ice_vvelocity",
        "output_type": "ice_currents",
        "has_layer": False,
        "parameterCategory": 2,
        "parameterCategoryName": "Ice",
        "parameterNumber": 4,
        "parameterNumberName": "Ice_drift_v_velocity",
        "parameterUnit": "m.s-1",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
    },
]

PROG_VARIABLE_SPECS: List[Dict] = [
    {
        "name": "sst",
        "output_type": "prog_temperature",
        "parameterCategory": 2,
        "parameterCategoryName": "Temperature",
        "parameterNumber": 0,
        "parameterNumberName": "Sea_surface_temperature",
        "parameterUnit": "degC",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
        "has_layer": True,
    },
    {
        "name": "sss",
        "output_type": "prog_salinity",
        "parameterCategory": 3,
        "parameterCategoryName": "Mass",
        "parameterNumber": 1,
        "parameterNumberName": "Sea_surface_salinity",
        "parameterUnit": "psu",
        "surface1Type": 1,
        "surface1TypeName": "Ground or water surface",
        "surface1Value": 0,
        "has_layer": True,
    },
    {
        "name": "u_velocity",
        "output_type": "prog_currents",
        "parameterCategory": 1,
        "parameterCategoryName": "Currents",
        "parameterNumber": 2,
        "parameterNumberName": "U_component_of_current",
        "parameterUnit": "m.s-1",
        "surface1Type": 160,
        "surface1TypeName": "Depth below sea level",
        "surface1Value": 0,
        "has_layer": True,
    },
    {
        "name": "v_velocity",
        "output_type": "prog_currents",
        "parameterCategory": 1,
        "parameterCategoryName": "Currents",
        "parameterNumber": 3,
        "parameterNumberName": "V_component_of_current",
        "parameterUnit": "m.s-1",
        "surface1Type": 160,
        "surface1TypeName": "Depth below sea level",
        "surface1Value": 0,
        "has_layer": True,
    },
]

batch_data: List[Tuple] = []
current_data: List[Tuple] = []


def normalize_lon_180(lon: float) -> float:
    """경도를 [-180, 180] 범위로 정규화합니다."""
    lon_360 = lon % 360.0
    return ((lon_360 + 180.0) % 360.0) - 180.0


def normalize_lon_180_array(lon_arr: np.ndarray) -> np.ndarray:
    """경도 배열을 [-180, 180) 범위로 정규화합니다."""
    lon_360 = np.mod(lon_arr, 360.0)
    return ((lon_360 + 180.0) % 360.0) - 180.0


def lat_lon_to_grid_index(lat: float, lon: float) -> Tuple[int, int, int]:
    """
    (lat, lon)을 가장 가까운 출력 격자 (row, col, flat_idx)로 변환합니다.
    TARGET_LONS: 0..359 (dx=1.0), TARGET_LATS: 90..-90 (dy=1.0).
    """
    lon_360 = float(np.mod(lon, 360.0))
    lat_clamped = float(np.clip(lat, float(TARGET_LATS[-1]), float(TARGET_LATS[0])))

    col = int(np.argmin(np.abs(TARGET_LONS - lon_360)))
    row = int(np.argmin(np.abs(TARGET_LATS - lat_clamped)))
    flat_idx = row * NX + col
    return row, col, flat_idx


def _clean_gdf_4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """지오메트리를 유효화하고 EPSG:4326을 세팅합니다."""
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry").set_crs(
            "EPSG:4326", allow_override=True
        )

    out = gdf[["geometry"]].copy()
    out["geometry"] = out.geometry.apply(make_valid)
    out = out[out.geometry.notna() & (~out.geometry.is_empty) & out.geometry.is_valid].copy()
    return gpd.GeoDataFrame(out, geometry="geometry").set_crs("EPSG:4326", allow_override=True)


def load_land_polygons(
        topo_path: str = DEFAULT_TOPO_PATH,
        polygon_layers: Tuple[str, ...] = POLYGON_LAYERS,
        fallback_coast_layer: str = FALLBACK_COAST_LAYER,
        buffer_meters: float = 0.0,
) -> gpd.GeoDataFrame:
    """land / countries 폴리곤 레이어를 우선 사용하고, 없으면 해안선 polygonize로 대체합니다."""
    warnings.filterwarnings("ignore", message=".*GeoSeries\\.notna.*", category=UserWarning)

    with open(topo_path, "r", encoding="utf-8") as f:
        topo_data = json.load(f)

    available_objects = list(topo_data.get("objects", {}).keys())
    gdfs: List[gpd.GeoDataFrame] = []

    for obj_name in polygon_layers:
        if obj_name in available_objects:
            gdf = tp.Topology(topo_data, object_name=obj_name).to_gdf()
            cleaned = _clean_gdf_4326(gdf)
            if not cleaned.empty:
                gdfs.append(cleaned)

    if not gdfs and fallback_coast_layer in available_objects:
        gdf_coast = tp.Topology(topo_data, object_name=fallback_coast_layer).to_gdf()
        polys = [make_valid(p) for p in polygonize(gdf_coast.geometry.union_all())]
        gdf_poly = _clean_gdf_4326(gpd.GeoDataFrame(geometry=polys))
        if not gdf_poly.empty:
            gdfs.append(gdf_poly)

    if not gdfs:
        raise ValueError(f"사용 가능한 육지 레이어가 없습니다. 존재 객체: {available_objects}")

    merged = pd.concat(gdfs, ignore_index=True)
    land_gdf = _clean_gdf_4326(merged)

    if buffer_meters != 0.0 and not land_gdf.empty:
        land_proj = land_gdf.to_crs("ESRI:54035")
        land_proj["geometry"] = land_proj.geometry.buffer(buffer_meters)
        land_gdf = _clean_gdf_4326(land_proj.to_crs("EPSG:4326"))

    return land_gdf[["geometry"]].reset_index(drop=True)


def build_target_ocean_grid_mask(land_gdf_raw: gpd.GeoDataFrame) -> np.ndarray:
    """
    목표 출력 격자(GRID_LON, GRID_LAT)에서 바다(True) / 육지(False) 2D 마스크를 생성합니다.
    지도에서 대륙에 값이 나타나는 것을 방지하기 위해 버퍼가 없는 원본 육지 폴리곤을 사용합니다.
    """
    grid_lon_180 = normalize_lon_180_array(GRID_LON.ravel())
    grid_lat_1d = GRID_LAT.ravel()

    grid_points_gdf = gpd.GeoDataFrame(
        {"grid_pos": np.arange(grid_lon_180.size)},
        geometry=gpd.points_from_xy(grid_lon_180, grid_lat_1d),
        crs="EPSG:4326",
    )

    joined_within = gpd.sjoin(
        grid_points_gdf[["grid_pos", "geometry"]],
        land_gdf_raw,
        how="inner",
        predicate="within",
    )
    joined_intersects = gpd.sjoin(
        grid_points_gdf[["grid_pos", "geometry"]],
        land_gdf_raw,
        how="inner",
        predicate="intersects",
    )

    land_pos = np.unique(
        np.concatenate([
            joined_within["grid_pos"].to_numpy(dtype=int),
            joined_intersects["grid_pos"].to_numpy(dtype=int),
        ])
    )

    ocean_flat = np.ones(grid_lon_180.size, dtype=bool)
    if land_pos.size > 0:
        ocean_flat[land_pos] = False

    ocean_mask_2d = ocean_flat.reshape(NY, NX)
    print(
        f"[목표 격자 육지 마스크 생성 완료] 전체={ocean_mask_2d.size:,}, "
        f"해양={int(ocean_mask_2d.sum()):,}, 육지(마스킹 대상)={int((~ocean_mask_2d).sum()):,}"
    )
    return ocean_mask_2d


def is_on_land(lat: float, lon: float, land_gdf: gpd.GeoDataFrame) -> bool:
    """주어진 (lat, lon)이 육지 폴리곤 내부에 포함되면 True, 아니면 False를 반환합니다."""
    pt = Point(normalize_lon_180(lon), lat)
    return bool(land_gdf.geometry.contains(pt).any())


def validate_land_gdf(land_gdf: gpd.GeoDataFrame) -> None:
    """육지 폴리곤 GeoDataFrame과 기준 좌표 판정 결과를 검증합니다."""
    print("=== 1. 폴리곤 레이어 검증 ===")
    print(f"CRS: {land_gdf.crs}")
    print(f"폴리곤 개수: {len(land_gdf)}")
    print(f"전체 경위도 bounds: {land_gdf.total_bounds.tolist()}")
    print(f"empty 개수: {int(land_gdf.geometry.is_empty.sum())}")
    print(f"invalid 개수: {int((~land_gdf.geometry.is_valid).sum())}")

    test_cases = [
        ("서울 (내륙)", 37.5665, 126.9780, True),
        ("안양 (내륙)", 37.3943, 126.9568, True),
        ("동해 (해상)", 36.5000, 131.0000, False),
        ("서울 경도 +360 정규화", 37.5665, 126.9780 + 360.0, True),
    ]

    print("\n=== 2. 기준 좌표 판정 검증 ===")
    all_passed = True
    for name, lat, lon, expected in test_cases:
        actual = is_on_land(lat, lon, land_gdf)
        passed = actual == expected
        all_passed = all_passed and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: lat={lat}, lon={lon} -> actual={actual} (expected={expected})")

    if not all_passed:
        raise AssertionError("기준 좌표 검증 실패: 폴리곤 레이어 또는 버퍼 값을 점검하세요.")


def verify_generated_grid_data(
        mode: str,
        output_dir: str,
        generated_files: List[str],
        in_memory_records: Optional[List[Tuple]] = None,
) -> None:
    """
    생성된 JSON 또는 DB 레코드에서 대표 내륙/해양 좌표의 격자 값을 점검합니다.
    내륙 좌표는 None이어야 하며, prog 해상은 유효 값이 존재해야 합니다.
    """
    print(f"\n=== 3. 생성 데이터 좌표 검증 (mode={mode}) ===")
    payloads: List[Tuple[str, List[Dict]]] = []

    if in_memory_records:
        for forecast_time, rec_mode, rec_type, _, _, grib_json_str in in_memory_records:
            if rec_mode == ("ice" if mode == "ice" else "ocean"):
                payloads.append((f"{forecast_time}:{rec_type}", json.loads(grib_json_str)))
    else:
        target_names = sorted(list(set(generated_files)))
        for fname in target_names:
            fpath = Path(output_dir) / fname
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    payloads.append((fname, json.load(f)))

    if not payloads:
        print("[검증 스킵] 점검할 생성 데이터가 없습니다.")
        return

    sample_label, sample_payload = payloads[0]
    print(f"[샘플 대상] {sample_label} (레코드 {len(sample_payload)}개, 전체 {len(payloads)}건 중 첫 항목 점검)")

    expected_len = NY * NX
    failures: List[str] = []

    for entry in sample_payload:
        header = entry.get("header", {})
        data = entry.get("data", [])
        param_name = header.get("parameterNumberName", "unknown")

        if len(data) != expected_len:
            failures.append(f"{param_name}: data 길이 불일치 ({len(data)} != {expected_len})")
            continue

        for label, lat, lon, expect_none_in_prog in VERIFICATION_COORDINATES:
            row, col, flat_idx = lat_lon_to_grid_index(lat, lon)
            val = data[flat_idx]
            grid_lat = float(TARGET_LATS[row])
            grid_lon = float(TARGET_LONS[col])

            if expect_none_in_prog:
                if val is not None:
                    failures.append(
                        f"{param_name} / {label} ({grid_lat}, {grid_lon}) [idx={flat_idx}]: "
                        f"내륙 좌표인데 값이 존재함 ({val})"
                    )
                status = "PASS" if val is None else "FAIL"
                print(
                    f"  [{status}] {param_name} | {label} -> grid({grid_lat}, {grid_lon}) "
                    f"idx={flat_idx}: value={val} (expected=None)"
                )
            elif mode == "prog":
                if val is None:
                    failures.append(
                        f"{param_name} / {label} ({grid_lat}, {grid_lon}) [idx={flat_idx}]: "
                        f"prog 해상 좌표인데 값이 None임"
                    )
                status = "PASS" if val is not None else "FAIL"
                print(
                    f"  [{status}] {param_name} | {label} -> grid({grid_lat}, {grid_lon}) "
                    f"idx={flat_idx}: value={val} (expected=non-None)"
                )
            else:
                # ice 모드는 무빙 해역(저위도/열대 등)에서 None이 정상일 수 있으므로 값만 로깅
                print(
                    f"  [INFO] {param_name} | {label} -> grid({grid_lat}, {grid_lon}) "
                    f"idx={flat_idx}: value={val}"
                )

    if failures:
        raise AssertionError("생성 데이터 좌표 검증 실패:\n - " + "\n - ".join(failures))

    print("[검증 완료] 대표 내륙/해양 좌표 점검을 통과했습니다.")


def extract_forecast_hour(file_path: Path) -> Optional[int]:
    match = re.search(r"_f(\d{3})_", file_path.name)
    if not match:
        return None
    return int(match.group(1))


def collect_rtofs_files(download_dir: str, mode: str) -> Dict[int, Path]:
    """download_dir에서 mode(ice/prog)에 해당하는 nc 파일을 수집하여 forecast_hour 오름차순으로 반환"""
    download_path = Path(download_dir)
    pattern = f"rtofs_glo_*_f???_{mode}.nc"
    files_by_hour: Dict[int, Path] = {}

    for file_path in sorted(download_path.glob(pattern)):
        forecast_hour = extract_forecast_hour(file_path)
        if forecast_hour is None:
            continue
        files_by_hour[forecast_hour] = file_path

    return dict(sorted(files_by_hour.items(), key=lambda item: item[0]))


def get_first_rtofs_file(files_by_hour: Dict[int, Path], mode: str) -> Tuple[int, Path]:
    for forecast_hour, nc_file in files_by_hour.items():
        if nc_file.exists():
            return forecast_hour, nc_file

    raise FileNotFoundError(f"fxxx 순서의 첫 번째 {mode}.nc 파일을 찾을 수 없습니다.")


def normalize_longitude(ds: xr.Dataset) -> np.ndarray:
    lon_raw = ds["Longitude"].compute()
    lon_360 = np.mod(lon_raw, 360.0)
    return lon_360.values.flatten()


def get_variable_2d(
        ds: xr.Dataset,
        variable_name: str,
        mt_idx: int,
        has_layer: bool,
) -> np.ndarray:
    if variable_name not in ds:
        raise KeyError(f"{variable_name} 변수가 파일에 없습니다.")

    if has_layer and "Layer" in ds[variable_name].dims:
        return ds[variable_name].isel(MT=mt_idx, Layer=0).compute().values.flatten()

    return ds[variable_name].isel(MT=mt_idx).compute().values.flatten()


def get_ice_ref_time() -> str:
    today_00_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today_00_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_ice_time_str(forecast_hour: int) -> str:
    today_00_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    forecast_time = today_00_utc + timedelta(hours=forecast_hour)
    return forecast_time.strftime("%Y%m%d%H")


def get_prog_ref_time(first_prog_file: Path, first_forecast_hour: int) -> str:
    with xr.open_dataset(first_prog_file, chunks={"MT": 1}) as ds:
        mt_array = pd.to_datetime(ds["MT"].compute().values)
        if mt_array.size == 0:
            raise ValueError(f"기준 prog 파일의 MT가 비어 있습니다: {first_prog_file}")

        ref_time = pd.to_datetime(mt_array[0]).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if first_forecast_hour != 0:
            cur_dt = datetime.fromisoformat(ref_time.replace("Z", "+00:00"))
            target_dt = cur_dt - timedelta(hours=first_forecast_hour)
            ref_time = pd.to_datetime(target_dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return ref_time


def build_shared_triangulation(
        first_nc_file: Path,
        variable_specs: List[Dict],
        topo_path: str = DEFAULT_TOPO_PATH,
        buffer_meters: float = DEFAULT_LAND_BUFFER_METERS,
) -> Dict:
    print(f"[공통 Delaunay 생성 기준 파일] {first_nc_file}")
    t0 = time.time()

    # 1. 원본 폴리곤 정합성 및 기준 좌표 검증
    land_gdf_raw = load_land_polygons(
        topo_path=topo_path,
        polygon_layers=POLYGON_LAYERS,
        fallback_coast_layer=FALLBACK_COAST_LAYER,
        buffer_meters=0.0,
    )
    validate_land_gdf(land_gdf_raw)

    # 2. 출력 격자(GRID_LON, GRID_LAT)용 육지/해양 마스크 생성 (원본 폴리곤 기준)
    ocean_grid_mask = build_target_ocean_grid_mask(land_gdf_raw)

    # 3. 입력 소스 격자 정점 필터링용 육지 폴리곤 생성 (버퍼 적용)
    land_gdf_4326 = load_land_polygons(
        topo_path=topo_path,
        polygon_layers=POLYGON_LAYERS,
        fallback_coast_layer=FALLBACK_COAST_LAYER,
        buffer_meters=buffer_meters,
    )

    with xr.open_dataset(first_nc_file, chunks={"MT": 1}) as ds:
        mt_array = pd.to_datetime(ds["MT"].compute().values)
        if mt_array.size == 0:
            raise ValueError(f"기준 파일의 MT가 비어 있습니다: {first_nc_file}")

        lon_raw = ds["Longitude"].compute().values.flatten()
        lat_raw = ds["Latitude"].compute().values.flatten()
        n_cells = lon_raw.size

        lon_360 = np.mod(lon_raw, 360.0)
        lon_180 = normalize_lon_180_array(lon_360)

        combined_valid_mask = np.zeros(n_cells, dtype=bool)
        checked_vars: List[str] = []

        for spec in variable_specs:
            var_name = spec["name"]
            if var_name not in ds:
                print(f"[경고] '{var_name}' 변수가 파일에 없어 제외합니다.")
                continue

            has_layer = bool(spec.get("has_layer", False))
            try:
                da = ds[var_name]
                if "MT" in da.dims:
                    da = da.isel(MT=0)
                if has_layer and "Layer" in da.dims:
                    da = da.isel(Layer=0)
                val_1d = da.squeeze().compute().values.flatten()
            except Exception as exc:
                print(f"[경고] '{var_name}' 변수 로드 실패로 제외합니다: {exc}")
                continue

            if val_1d.size != n_cells:
                print(
                    f"[경고] '{var_name}' 원소 수({val_1d.size})가 격자 수({n_cells})와 달라 제외합니다."
                )
                continue

            combined_valid_mask |= ~np.isnan(val_1d)
            checked_vars.append(var_name)

        print(f"[유효 마스크 생성 완료] 대상 변수({len(checked_vars)}개): {checked_vars}")

        valid_mask = combined_valid_mask & ~np.isnan(lat_raw) & ~np.isnan(lon_360)
        valid_indices = np.flatnonzero(valid_mask)

        if len(valid_indices) == 0:
            raise ValueError("공통 Delaunay 생성을 위한 유효 points가 없습니다.")

        valid_points_gdf = gpd.GeoDataFrame(
            {
                "valid_pos": np.arange(len(valid_indices)),
                "global_idx": valid_indices,
            },
            geometry=gpd.points_from_xy(lon_180[valid_indices], lat_raw[valid_indices]),
            crs="EPSG:4326",
        )

        points_in_land = gpd.sjoin(
            valid_points_gdf[["valid_pos", "geometry"]],
            land_gdf_4326,
            how="inner",
            predicate="within",
        )
        land_valid_pos = set(points_in_land["valid_pos"].unique())

        ocean_mask = np.ones(len(valid_indices), dtype=bool)
        if land_valid_pos:
            land_pos_arr = np.fromiter(land_valid_pos, dtype=int)
            ocean_mask[land_pos_arr] = False
            land_global_indices = valid_indices[land_pos_arr]
            valid_mask[land_global_indices] = False
            print(f"[육지 포인트 제외] land 영역 포함 {len(land_pos_arr):,}개 제외")

        ocean_global_indices = valid_indices[ocean_mask]
        if len(ocean_global_indices) == 0:
            raise ValueError("바다 영역에 포함된 유효 points가 없습니다.")

        points = np.column_stack((lon_360[ocean_global_indices], lat_raw[ocean_global_indices]))
        print(f"[바다 영역 유효 격자 추출 완료] points={len(points):,}개")

        print(f"[공통 Delaunay] points={len(points):,} 생성 중...")
        tri = Delaunay(points)

    elapsed = time.time() - t0
    print(f"[공통 Delaunay 완료] 소요 시간: {elapsed:.2f}초")

    return {
        "tri": tri,
        "valid_mask": valid_mask,
        "points": points,
        "ocean_grid_mask": ocean_grid_mask,
        "total_cells": int(valid_mask.size),
        "source_file": str(first_nc_file),
    }


def save_shared_grid(shared_grid: Dict, shared_grid_path: str) -> Path:
    grid_file = Path(shared_grid_path)
    grid_file.parent.mkdir(parents=True, exist_ok=True)

    with open(grid_file, "wb") as f:
        pickle.dump(shared_grid, f, protocol=pickle.HIGHEST_PROTOCOL)

    size_mb = grid_file.stat().st_size / 1024 / 1024
    print(f"[shared_grid 저장 완료] {grid_file} ({size_mb:.2f} MB)")
    return grid_file


def ensure_ocean_grid_mask(
        shared_grid: Dict,
        topo_path: str = DEFAULT_TOPO_PATH,
) -> np.ndarray:
    """shared_grid에 ocean_grid_mask가 있으면 반환하고, 없으면 원본 폴리곤으로 생성합니다."""
    mask = shared_grid.get("ocean_grid_mask")
    if mask is not None:
        arr = np.asarray(mask, dtype=bool)
        if arr.shape == (NY, NX):
            shared_grid["ocean_grid_mask"] = arr
            return arr

    print("[경고] shared_grid에 ocean_grid_mask가 없어 목표 격자 마스크를 생성합니다.")
    land_gdf_raw = load_land_polygons(
        topo_path=topo_path,
        polygon_layers=POLYGON_LAYERS,
        fallback_coast_layer=FALLBACK_COAST_LAYER,
        buffer_meters=0.0,
    )
    arr = build_target_ocean_grid_mask(land_gdf_raw)
    shared_grid["ocean_grid_mask"] = arr
    return arr


def load_shared_grid(
        shared_grid_path: str,
        topo_path: str = DEFAULT_TOPO_PATH,
) -> Dict:
    grid_file = Path(shared_grid_path)
    if not grid_file.exists():
        raise FileNotFoundError(f"shared_grid 파일을 찾을 수 없습니다: {grid_file}")

    with open(grid_file, "rb") as f:
        shared_grid = pickle.load(f)

    required_keys = {"tri", "valid_mask", "points"}
    missing = required_keys - set(shared_grid.keys())
    if missing:
        raise ValueError(f"shared_grid 필수 키가 누락되었습니다: {sorted(missing)}")

    valid_mask = np.asarray(shared_grid["valid_mask"], dtype=bool)
    points = np.asarray(shared_grid["points"])
    tri: Delaunay = shared_grid["tri"]

    if valid_mask.ndim != 1:
        raise ValueError(f"valid_mask는 1차원 배열이어야 합니다. 현재 ndim={valid_mask.ndim}")
    if int(valid_mask.sum()) != len(points) or len(points) != tri.npoints:
        raise ValueError(
            f"shared_grid 내부 정합성 오류: valid_mask.sum()={int(valid_mask.sum())}, "
            f"len(points)={len(points)}, tri.npoints={tri.npoints}"
        )

    shared_grid["valid_mask"] = valid_mask
    shared_grid["points"] = points
    ensure_ocean_grid_mask(shared_grid, topo_path=topo_path)

    print(f"[shared_grid 로드 완료] {grid_file} (points={len(points)})")
    return shared_grid


def get_or_create_shared_grid(
        shared_grid_path: str,
        first_nc_file: Path,
        variable_specs: List[Dict],
        topo_path: str = DEFAULT_TOPO_PATH,
        buffer_meters: float = DEFAULT_LAND_BUFFER_METERS,
        rebuild: bool = False,
) -> Dict:
    grid_file = Path(shared_grid_path)
    if grid_file.exists() and not rebuild:
        return load_shared_grid(shared_grid_path, topo_path=topo_path)

    shared_grid = build_shared_triangulation(
        first_nc_file=first_nc_file,
        variable_specs=variable_specs,
        topo_path=topo_path,
        buffer_meters=buffer_meters,
    )
    save_shared_grid(shared_grid, shared_grid_path)
    return shared_grid


def validate_dataset_grid_compatibility(
        ds: xr.Dataset,
        shared_grid: Dict,
        file_path: Path,
        check_coords: bool = False,
        coord_atol: float = 1e-4,
) -> None:
    valid_mask: np.ndarray = shared_grid["valid_mask"]
    points: np.ndarray = shared_grid["points"]
    tri: Delaunay = shared_grid["tri"]

    if "Latitude" not in ds or "Longitude" not in ds:
        raise KeyError(f"좌표 변수(Latitude/Longitude)가 없습니다: {file_path.name}")

    lat_flat = ds["Latitude"].compute().values.flatten()
    lon_flat = normalize_longitude(ds)

    if lat_flat.size != valid_mask.size or lon_flat.size != valid_mask.size:
        raise ValueError(
            f"좌표 격자 크기 불일치 ({file_path.name}): "
            f"lat={lat_flat.size}, lon={lon_flat.size}, valid_mask={valid_mask.size}"
        )

    masked_points = np.column_stack((lon_flat[valid_mask], lat_flat[valid_mask]))
    if masked_points.shape != points.shape:
        raise ValueError(
            f"유효 포인트 배열 형태 불일치 ({file_path.name}): "
            f"current={masked_points.shape}, shared={points.shape}"
        )

    if masked_points.shape[0] != tri.npoints:
        raise ValueError(
            f"유효 포인트 수와 Delaunay 정점 수 불일치 ({file_path.name}): "
            f"current={masked_points.shape[0]}, tri.npoints={tri.npoints}"
        )

    if check_coords and not np.allclose(masked_points, points, atol=coord_atol, equal_nan=False):
        raise ValueError(
            f"유효 좌표(lon, lat) 값 불일치 ({file_path.name}, atol={coord_atol}). "
            f"--rebuild_shared_grid 옵션으로 재생성 여부를 확인하세요."
        )


def validate_variable_compatibility(
        raw_1d: np.ndarray,
        shared_grid: Dict,
        file_path: Path,
        variable_name: str,
) -> np.ndarray:
    valid_mask: np.ndarray = shared_grid["valid_mask"]
    tri: Delaunay = shared_grid["tri"]

    if raw_1d.ndim != 1:
        raise ValueError(f"{variable_name} 배열은 1차원이어야 합니다. ndim={raw_1d.ndim}")

    if raw_1d.size != valid_mask.size:
        raise ValueError(
            f"변수 격자 셀 수 불일치 ({file_path.name} / {variable_name}): "
            f"raw={raw_1d.size}, valid_mask={valid_mask.size}"
        )

    values = raw_1d[valid_mask].astype(np.float64)
    if values.size != tri.npoints:
        raise ValueError(
            f"마스킹 후 유효 값 수 불일치 ({file_path.name} / {variable_name}): "
            f"values={values.size}, tri.npoints={tri.npoints}"
        )

    return values


def to_json_data(grid: np.ndarray, round_digits: int = 2) -> List[Optional[float]]:
    rounded = np.round(grid, round_digits).flatten()
    nan_mask = np.isnan(rounded)
    return [None if is_nan else float(value) for value, is_nan in zip(rounded, nan_mask)]


def build_header(
        spec: Dict,
        ref_time: str,
        forecast_time: int,
        data_length: int,
) -> Dict:
    return {
        "discipline": 10,
        "disciplineName": "Oceanographic products",
        "center": -3,
        "centerName": "National Centers for Environmental Prediction",
        "refTime": ref_time,
        "forecastTime": forecast_time,
        "parameterCategory": spec["parameterCategory"],
        "parameterCategoryName": spec["parameterCategoryName"],
        "parameterNumber": spec["parameterNumber"],
        "parameterNumberName": spec["parameterNumberName"],
        "parameterUnit": spec["parameterUnit"],
        "surface1Type": spec["surface1Type"],
        "surface1TypeName": spec["surface1TypeName"],
        "surface1Value": spec["surface1Value"],
        "numberPoints": data_length,
        "nx": NX,
        "ny": NY,
        "lo1": float(TARGET_LONS[0]),
        "la1": float(TARGET_LATS[0]),
        "lo2": float(TARGET_LONS[-1]),
        "la2": float(TARGET_LATS[-1]),
        "dx": 1.0,
        "dy": 1.0,
    }


def interpolate_variable(
        ds: xr.Dataset,
        spec: Dict,
        mt_idx: int,
        shared_grid: Dict,
        file_path: Path,
) -> List[Optional[float]]:
    raw = get_variable_2d(
        ds=ds,
        variable_name=spec["name"],
        mt_idx=mt_idx,
        has_layer=spec["has_layer"],
    )

    values = validate_variable_compatibility(
        raw_1d=raw,
        shared_grid=shared_grid,
        file_path=file_path,
        variable_name=spec["name"],
    )

    # 1. 변수별 NaN 격리 보간
    finite_mask = np.isfinite(values)
    nan_count = int((~finite_mask).sum())
    if nan_count == 0:
        interpolator = LinearNDInterpolator(shared_grid["tri"], values)
    else:
        if int(finite_mask.sum()) < 4:
            raise ValueError(f"{spec['name']} 유효(finite) 관측치가 부족합니다.")
        interpolator = LinearNDInterpolator(
            shared_grid["points"][finite_mask],
            values[finite_mask],
        )

    grid = np.asarray(interpolator(GRID_LON, GRID_LAT), dtype=np.float64)

    # 2. 목표 격자에서 육지/대륙 영역을 NaN(None)으로 마스킹
    ocean_grid_mask: np.ndarray = shared_grid["ocean_grid_mask"]
    grid[~ocean_grid_mask] = np.nan

    json_data = to_json_data(grid)
    non_null_count = sum(1 for v in json_data if v is not None)
    """
    print(
        f"    - [{spec['name']}] nan_in_src={nan_count}, "
        f"masked_land={int((~ocean_grid_mask).sum())}, valid_ocean_out={non_null_count}"
    )
    """

    return json_data


def save_payload(
        output_dir: str,
        mode: str,
        time_str: str,
        output_type: str,
        payload: List[Dict],
) -> str:
    output_filename = f"{time_str}-surface-{output_type}-rtofs-1.0.json"
    output_path = Path(output_dir) / output_filename

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"[저장 완료] {mode} / {output_type}: {output_filename} ({size_mb:.2f} MB)")

    return output_filename


def insert_payload(conn: sqlite3.Connection) -> None:
    try:
        query_batch = """INSERT INTO Earth_Tbl (forecast_time, mode, type, surface, level, grib2json)
                         VALUES (?, ?, ?, ?, ?, ?)
                             ON CONFLICT(forecast_time, mode, type, surface, level) DO UPDATE SET
            grib2json = excluded.grib2json"""
        conn.cursor().executemany(query_batch, batch_data)
        conn.commit()

        query_current = """INSERT INTO Earth_Current_Tbl (forecast_time, mode, type, surface, level, grib2json)
                           VALUES (?, ?, ?, ?, ?, ?)
                               ON CONFLICT(forecast_time, mode, type, surface, level) DO UPDATE SET
            grib2json = excluded.grib2json"""
        conn.cursor().executemany(query_current, current_data)
        conn.commit()
        print(f"batch_data[{len(batch_data)}], current_data[{len(current_data)}]: 데이터가 Insert 되었습니다.")

    except Exception as e:
        print(f"데이터 Insert 중 오류 발생: {e}")


def process_rtofs_file(
        mode: str,
        file_info: Tuple[Path, str, int],
        variable_specs: List[Dict],
        shared_grid: Dict,
        output_dir: str,
        mt_range: Optional[Tuple[int, int]] = None,
        check_coords: bool = False,
        conn: Optional[sqlite3.Connection] = None,
) -> List[str]:
    generated_files: List[str] = []
    file_path, ref_time, forecast_hour = file_info
    db_mode = "ice" if mode == "ice" else "ocean"

    print(f"[{mode} 파일 처리 시작] file={file_path.name}, ref_time={ref_time}, forecast_hour={forecast_hour}")

    with xr.open_dataset(file_path, chunks={"MT": 1}) as ds:
        validate_dataset_grid_compatibility(
            ds=ds,
            shared_grid=shared_grid,
            file_path=file_path,
            check_coords=check_coords,
        )

        mt_raw = ds["MT"].compute().values
        mt_array = pd.to_datetime(mt_raw)

        if mt_array.size == 0:
            print(f"[스킵] MT가 비어 있습니다: {file_path}")
            return generated_files

        start_idx = 0
        end_idx = mt_array.size - 1

        if mt_range is not None:
            start_idx = max(mt_range[0], 0)
            end_idx = min(mt_range[1], mt_array.size - 1)

        if start_idx > end_idx:
            print(f"[스킵] 처리 가능한 MT 범위가 없습니다: {file_path}")
            return generated_files

        grouped_specs: Dict[str, List[Dict]] = {}
        for spec in variable_specs:
            if spec["name"] not in ds:
                print(f"[변수 없음] {file_path.name}: {spec['name']} 스킵")
                continue
            grouped_specs.setdefault(spec["output_type"], []).append(spec)

        for mt_idx in range(start_idx, end_idx + 1):
            t0 = time.time()

            if mode == "ice":
                time_str = get_ice_time_str(forecast_hour)
            else:
                time_str = pd.to_datetime(mt_array[mt_idx]).strftime("%Y%m%d%H")

            print(
                f" MT={mt_idx}/{mt_array.size - 1}, ref_time={ref_time}, "
                f"forecast_time={time_str}, forecast_hour={forecast_hour}"
            )

            for output_type, output_specs in grouped_specs.items():
                payload: List[Dict] = []

                for spec in output_specs:
                    try:
                        data = interpolate_variable(
                            ds=ds,
                            spec=spec,
                            mt_idx=mt_idx,
                            shared_grid=shared_grid,
                            file_path=file_path,
                        )
                        payload.append(
                            {
                                "header": build_header(
                                    spec=spec,
                                    ref_time=ref_time,
                                    forecast_time=forecast_hour,
                                    data_length=len(data),
                                ),
                                "data": data,
                            }
                        )
                    except Exception as error:
                        print(f"[변수 처리 오류] {file_path.name} / {spec['name']}: {error}")
                    finally:
                        gc.collect()

                if payload:
                    if conn:
                        record = (time_str, db_mode, output_type, "surface", "level", json.dumps(payload))
                        batch_data.append(record)
                        if forecast_hour == 0:
                            current_data.append(record)
                    else:
                        output_filename = save_payload(
                            output_dir=output_dir,
                            mode=mode,
                            time_str=time_str,
                            output_type=output_type,
                            payload=payload,
                        )
                        generated_files.append(output_filename)

            print(f"[MT 완료] 소요 시간: {time.time() - t0:.2f}초")
            gc.collect()

    print(f"[{mode} 파일 처리 완료] {file_path.name}")
    return generated_files


def merge_and_overwrite(target_list: List[str], new_list: List[str]) -> List[str]:
    filtered_target = [item for item in target_list if item not in new_list]
    return filtered_target + new_list


def update_catalog(output_dir: str, generated_files: List[str]) -> None:
    if not generated_files:
        print("[catalog] 생성된 파일이 없어 catalog.json을 갱신하지 않습니다.")
        return

    catalog_path = Path(output_dir) / "catalog.json"

    if catalog_path.exists():
        with open(catalog_path, "r", encoding="utf-8") as file:
            catalog = json.load(file)
    else:
        catalog = {"files": [], "total_count": 0}

    generated_files = sorted(list(set(generated_files)))
    merged_files = merge_and_overwrite(catalog.get("files", []), generated_files)

    catalog_data = {
        "files": merged_files,
        "total_count": len(merged_files),
    }

    with open(catalog_path, "w", encoding="utf-8") as file:
        json.dump(catalog_data, file, ensure_ascii=False, indent=4)

    print(f"[catalog 저장 완료] {catalog_path} / total_count={len(merged_files)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert RTOFS ice.nc or prog.nc files to GRIB2 JSON using a reusable shared_grid file."
    )
    parser.add_argument(
        "--mode",
        type=str,
        required=True,
        choices=["ice", "prog"],
        help="Target RTOFS dataset mode: ice or prog",
    )
    parser.add_argument(
        "--download_dir",
        type=str,
        default=DEFAULT_DOWNLOAD_DIR,
        help="RTOFS NetCDF download directory",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for JSON files",
    )
    parser.add_argument(
        "--shared_grid_path",
        type=str,
        default=None,
        help="Path to save/load reusable shared_grid pickle file (default depends on --mode)",
    )
    parser.add_argument(
        "--topo_path",
        type=str,
        default=DEFAULT_TOPO_PATH,
        help="Path to TopoJSON land/coastline file",
    )
    parser.add_argument(
        "--buffer_meters",
        type=float,
        default=DEFAULT_LAND_BUFFER_METERS,
        help="Land polygon buffer in meters (default: -1000.0)",
    )
    parser.add_argument(
        "--rebuild_shared_grid",
        action="store_true",
        help="Force rebuild and overwrite shared_grid file even if it already exists",
    )
    parser.add_argument(
        "--check_coords",
        action="store_true",
        help="Verify masked (lon, lat) coordinates against shared_grid points",
    )
    parser.add_argument(
        "--verify_data",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify inland/ocean sample coordinates on generated data (default: True)",
    )
    parser.add_argument(
        "--mt_range",
        type=int,
        nargs=2,
        default=None,
        help="Optional MT index range: --mt_range 0 0",
    )
    parser.add_argument(
        "--useDb",
        type=str,
        default="Y",
        choices=["Y", "N"],
        help="Whether a database insert occurred (default: Y)",
    )
    parser.add_argument(
        "--db_path",
        type=str,
        default=DEFAULT_DB_PATH,
        help="SQLite database file path",
    )

    args = parser.parse_args()

    default_grid = DEFAULT_ICE_GRID_PATH if args.mode == "ice" else DEFAULT_PROG_GRID_PATH
    shared_grid_path = args.shared_grid_path or default_grid
    variable_specs = ICE_VARIABLE_SPECS if args.mode == "ice" else PROG_VARIABLE_SPECS

    conn: Optional[sqlite3.Connection] = None
    if args.useDb == "Y":
        conn = sqlite3.connect(args.db_path)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    files_by_hour = collect_rtofs_files(args.download_dir, args.mode)
    if not files_by_hour:
        raise FileNotFoundError(f"처리할 RTOFS {args.mode}.nc 파일이 없습니다: {args.download_dir}")

    first_hour, first_nc_file = get_first_rtofs_file(files_by_hour, args.mode)
    print(f"[첫 번째 {args.mode}.nc] f{first_hour:03d}: {first_nc_file.name}")

    if args.mode == "ice":
        ref_time = get_ice_ref_time()
    else:
        ref_time = get_prog_ref_time(first_nc_file, first_hour)

    shared_grid = get_or_create_shared_grid(
        shared_grid_path=shared_grid_path,
        first_nc_file=first_nc_file,
        variable_specs=variable_specs,
        topo_path=args.topo_path,
        buffer_meters=args.buffer_meters,
        rebuild=args.rebuild_shared_grid,
    )

    all_generated_files: List[str] = []

    for forecast_hour, nc_file in files_by_hour.items():
        print(f"\n========== f{forecast_hour:03d} ({args.mode}) 처리 시작 ==========")
        try:
            generated_files = process_rtofs_file(
                mode=args.mode,
                file_info=(nc_file, ref_time, forecast_hour),
                variable_specs=variable_specs,
                shared_grid=shared_grid,
                output_dir=args.output_dir,
                mt_range=tuple(args.mt_range) if args.mt_range else None,
                check_coords=args.check_coords,
                conn=conn,
            )
            all_generated_files.extend(generated_files)
        except Exception as error:
            print(f"[파일 처리 오류] {nc_file}: {error}")
        finally:
            gc.collect()
        print(f"========== f{forecast_hour:03d} ({args.mode}) 처리 완료 ==========")

    if args.verify_data:
        verify_generated_grid_data(
            mode=args.mode,
            output_dir=args.output_dir,
            generated_files=all_generated_files,
            in_memory_records=current_data if conn else None,
        )

    if conn:
        insert_payload(conn)
        if args.mode == "ice":
            cutoff_time = get_ice_time_str(0)
            conn.cursor().execute(
                "DELETE FROM Earth_Current_Tbl WHERE mode = ? AND type LIKE ? AND forecast_time < ?",
                ("ice", "ice%", cutoff_time),
            )
        else:
            cutoff_time = pd.to_datetime(ref_time).strftime("%Y%m%d%H")
            conn.cursor().execute(
                "DELETE FROM Earth_Current_Tbl WHERE mode = ? AND type LIKE ? AND forecast_time < ?",
                ("ocean", "prog%", cutoff_time),
            )
        conn.commit()
        conn.close()
    else:
        update_catalog(args.output_dir, all_generated_files)

    print("[작업 종료]")


if __name__ == "__main__":
    main()
