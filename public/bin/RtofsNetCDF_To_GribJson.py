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


# ---------------------------------------------------------------------------
# 좌표 / 격자 유틸리티
# ---------------------------------------------------------------------------

def normalize_lon_180(lon: float) -> float:
    lon_360 = lon % 360.0
    return ((lon_360 + 180.0) % 360.0) - 180.0


def normalize_lon_180_array(lon_arr: np.ndarray) -> np.ndarray:
    lon_360 = np.mod(lon_arr, 360.0)
    return ((lon_360 + 180.0) % 360.0) - 180.0


def normalize_longitude(ds: xr.Dataset) -> np.ndarray:
    lon_360 = np.mod(ds["Longitude"].compute(), 360.0)
    return lon_360.values.flatten()


def lat_lon_to_grid_index(lat: float, lon: float) -> Tuple[int, int, int]:
    lon_360 = float(np.mod(lon, 360.0))
    lat_clamped = float(np.clip(lat, float(TARGET_LATS[-1]), float(TARGET_LATS[0])))
    col = int(np.argmin(np.abs(TARGET_LONS - lon_360)))
    row = int(np.argmin(np.abs(TARGET_LATS - lat_clamped)))
    return row, col, row * NX + col


# ---------------------------------------------------------------------------
# TopoJSON 육지 폴리곤 및 해양 마스크 생성
# ---------------------------------------------------------------------------

def _clean_gdf_4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        empty = gpd.GeoDataFrame(columns=["geometry"], geometry="geometry")
        return empty.set_crs("EPSG:4326", allow_override=True)

    cleaned = gdf[["geometry"]].copy()
    cleaned["geometry"] = cleaned.geometry.apply(make_valid)
    cleaned = cleaned[
        cleaned.geometry.notna() & (~cleaned.geometry.is_empty) & cleaned.geometry.is_valid
        ].copy()
    return gpd.GeoDataFrame(cleaned, geometry="geometry").set_crs("EPSG:4326", allow_override=True)


def load_land_polygons(
        topo_path: str = DEFAULT_TOPO_PATH,
        polygon_layers: Tuple[str, ...] = POLYGON_LAYERS,
        fallback_coast_layer: str = FALLBACK_COAST_LAYER,
        buffer_meters: float = 0.0,
) -> gpd.GeoDataFrame:
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

    land_gdf = _clean_gdf_4326(pd.concat(gdfs, ignore_index=True))

    if buffer_meters != 0.0 and not land_gdf.empty:
        land_proj = land_gdf.to_crs("ESRI:54035")
        land_proj["geometry"] = land_proj.geometry.buffer(buffer_meters)
        land_gdf = _clean_gdf_4326(land_proj.to_crs("EPSG:4326"))

    return land_gdf[["geometry"]].reset_index(drop=True)


def build_target_ocean_grid_mask(land_gdf_raw: gpd.GeoDataFrame) -> np.ndarray:
    """출력 격자(GRID_LON, GRID_LAT) 기준 해양(True) / 육지(False) 2D 마스크를 생성합니다."""
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
    pt = Point(normalize_lon_180(lon), lat)
    return bool(land_gdf.geometry.contains(pt).any())


def validate_land_gdf(land_gdf: gpd.GeoDataFrame) -> None:
    print("=== 1. 폴리곤 레이어 검증 ===")
    print(f"CRS: {land_gdf.crs}")
    print(f"폴리곤 개수: {len(land_gdf)}")
    print(f"전체 경위도 bounds: {land_gdf.total_bounds.tolist()}")

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


# ---------------------------------------------------------------------------
# 사전 계산 보간 가중치(Barycentric Weights) 및 shared_grid 관리
# ---------------------------------------------------------------------------

def build_ocean_interpolation_weights(
        tri: Delaunay,
        ocean_grid_mask: np.ndarray,
) -> Dict[str, np.ndarray]:
    """
    해양 출력 격자점에 대해서만 Delaunay 삼각형 정점 인덱스와 무게중심 가중치를 계산합니다.
    삼각형 외부 격자점은 simplex=-1로 표시되어 NaN 처리됩니다.
    """
    ocean_flat = ocean_grid_mask.ravel()
    ocean_indices = np.flatnonzero(ocean_flat)
    target_pts = np.column_stack((GRID_LON.ravel()[ocean_indices], GRID_LAT.ravel()[ocean_indices]))

    simplex_ids = tri.find_simplex(target_pts)
    valid_mask = simplex_ids >= 0
    valid_simplices = simplex_ids[valid_mask]
    vertices = tri.simplices[valid_simplices]  # (M, 3)

    # transform[s] shape: (3, 3) -> [:2, :2]는 역행렬, [2, :]는 세 번째 정점 좌표
    transforms = tri.transform[valid_simplices]
    deltas = target_pts[valid_mask] - transforms[:, 2, :]
    bary_01 = np.einsum("ijk,ik->ij", transforms[:, :2, :], deltas)
    bary_2 = 1.0 - np.sum(bary_01, axis=1, keepdims=True)
    weights = np.hstack((bary_01, bary_2))

    return {
        "ocean_indices": ocean_indices,
        "valid_mask": valid_mask,
        "vertices": vertices,
        "weights": weights,
    }


def _extract_valid_ocean_points(
        ds: xr.Dataset,
        variable_specs: List[Dict],
        land_gdf_4326: gpd.GeoDataFrame,
) -> Tuple[np.ndarray, np.ndarray]:
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
            print(f"[경고] '{var_name}' 원소 수({val_1d.size})가 격자 수({n_cells})와 달라 제외합니다.")
            continue

        combined_valid_mask |= ~np.isnan(val_1d)
        checked_vars.append(var_name)

    print(f"[유효 마스크 생성 완료] 대상 변수({len(checked_vars)}개): {checked_vars}")

    valid_mask = combined_valid_mask & ~np.isnan(lat_raw) & ~np.isnan(lon_360)
    valid_indices = np.flatnonzero(valid_mask)
    if valid_indices.size == 0:
        raise ValueError("공통 Delaunay 생성을 위한 유효 points가 없습니다.")

    valid_points_gdf = gpd.GeoDataFrame(
        {"valid_pos": np.arange(valid_indices.size)},
        geometry=gpd.points_from_xy(lon_180[valid_indices], lat_raw[valid_indices]),
        crs="EPSG:4326",
    )

    points_in_land = gpd.sjoin(
        valid_points_gdf[["valid_pos", "geometry"]],
        land_gdf_4326,
        how="inner",
        predicate="within",
    )
    land_valid_pos = np.unique(points_in_land["valid_pos"].to_numpy(dtype=int))

    if land_valid_pos.size > 0:
        valid_mask[valid_indices[land_valid_pos]] = False
        print(f"[육지 포인트 제외] land 영역 포함 {land_valid_pos.size:,}개 제외")

    ocean_global_indices = np.flatnonzero(valid_mask)
    if ocean_global_indices.size == 0:
        raise ValueError("바다 영역에 포함된 유효 points가 없습니다.")

    points = np.column_stack((lon_360[ocean_global_indices], lat_raw[ocean_global_indices]))
    return valid_mask, points


def build_shared_triangulation(
        first_nc_file: Path,
        variable_specs: List[Dict],
        topo_path: str = DEFAULT_TOPO_PATH,
        buffer_meters: float = DEFAULT_LAND_BUFFER_METERS,
) -> Dict:
    print(f"[공통 Delaunay 생성 기준 파일] {first_nc_file}")
    t0 = time.time()

    land_gdf_raw = load_land_polygons(topo_path=topo_path, buffer_meters=0.0)
    validate_land_gdf(land_gdf_raw)
    ocean_grid_mask = build_target_ocean_grid_mask(land_gdf_raw)

    land_gdf_buffered = load_land_polygons(topo_path=topo_path, buffer_meters=buffer_meters)

    with xr.open_dataset(first_nc_file, chunks={"MT": 1}) as ds:
        mt_array = pd.to_datetime(ds["MT"].compute().values)
        if mt_array.size == 0:
            raise ValueError(f"기준 파일의 MT가 비어 있습니다: {first_nc_file}")

        valid_mask, points = _extract_valid_ocean_points(ds, variable_specs, land_gdf_buffered)
        print(f"[바다 영역 유효 격자 추출 완료] points={len(points):,}개")

        print(f"[공통 Delaunay] points={len(points):,} 생성 중...")
        tri = Delaunay(points)
        interp_weights = build_ocean_interpolation_weights(tri, ocean_grid_mask)

    print(f"[공통 Delaunay 완료] 소요 시간: {time.time() - t0:.2f}초")
    return {
        "tri": tri,
        "valid_mask": valid_mask,
        "points": points,
        "ocean_grid_mask": ocean_grid_mask,
        "interp_weights": interp_weights,
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


def ensure_shared_grid_assets(shared_grid: Dict, topo_path: str = DEFAULT_TOPO_PATH) -> Dict:
    mask = shared_grid.get("ocean_grid_mask")
    if mask is None or np.asarray(mask).shape != (NY, NX):
        land_gdf_raw = load_land_polygons(topo_path=topo_path, buffer_meters=0.0)
        mask = build_target_ocean_grid_mask(land_gdf_raw)
        shared_grid["ocean_grid_mask"] = mask
    else:
        mask = np.asarray(mask, dtype=bool)
        shared_grid["ocean_grid_mask"] = mask

    if "interp_weights" not in shared_grid:
        shared_grid["interp_weights"] = build_ocean_interpolation_weights(shared_grid["tri"], mask)

    return shared_grid


def load_shared_grid(shared_grid_path: str, topo_path: str = DEFAULT_TOPO_PATH) -> Dict:
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

    if valid_mask.ndim != 1 or int(valid_mask.sum()) != len(points) or len(points) != tri.npoints:
        raise ValueError("shared_grid 내부 배열 크기 또는 정점 수가 일치하지 않습니다.")

    shared_grid["valid_mask"] = valid_mask
    shared_grid["points"] = points
    ensure_shared_grid_assets(shared_grid, topo_path=topo_path)

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


# ---------------------------------------------------------------------------
# 데이터셋 호환성 검증 및 보간 실행
# ---------------------------------------------------------------------------

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
    if masked_points.shape != points.shape or masked_points.shape[0] != tri.npoints:
        raise ValueError(f"유효 포인트 수와 Delaunay 정점 수 불일치 ({file_path.name})")

    if check_coords and not np.allclose(masked_points, points, atol=coord_atol, equal_nan=False):
        raise ValueError(
            f"유효 좌표(lon, lat) 값 불일치 ({file_path.name}, atol={coord_atol}). "
            f"--rebuild_shared_grid 옵션으로 재생성 여부를 확인하세요."
        )


def get_variable_1d(ds: xr.Dataset, variable_name: str, mt_idx: int, has_layer: bool) -> np.ndarray:
    if variable_name not in ds:
        raise KeyError(f"{variable_name} 변수가 파일에 없습니다.")

    da = ds[variable_name].isel(MT=mt_idx)
    if has_layer and "Layer" in da.dims:
        da = da.isel(Layer=0)
    return da.squeeze().compute().values.flatten()


def interpolate_to_ocean_grid(values: np.ndarray, shared_grid: Dict) -> np.ndarray:
    """
    사전 계산된 무게중심 가중치를 우선 사용해 해양 격자만 빠르게 보간합니다.
    변수 내부에 NaN이 포함된 경우에만 유효 관측치 기반 LinearNDInterpolator를 사용합니다.
    """
    grid = np.full(NY * NX, np.nan, dtype=np.float64)
    ocean_grid_mask: np.ndarray = shared_grid["ocean_grid_mask"]
    ocean_indices: np.ndarray = shared_grid["interp_weights"]["ocean_indices"]

    finite_mask = np.isfinite(values)
    if not np.any(finite_mask):
        return grid.reshape(NY, NX)

    if np.all(finite_mask):
        w_info = shared_grid["interp_weights"]
        valid_mask = w_info["valid_mask"]
        vertices = w_info["vertices"]
        weights = w_info["weights"]

        interpolated_ocean = np.full(ocean_indices.size, np.nan, dtype=np.float64)
        interpolated_ocean[valid_mask] = np.sum(values[vertices] * weights, axis=1)
        grid[ocean_indices] = interpolated_ocean
    else:
        if int(finite_mask.sum()) < 4:
            raise ValueError("유효(finite) 관측치가 부족합니다.")
        interpolator = LinearNDInterpolator(shared_grid["points"][finite_mask], values[finite_mask])
        target_pts = np.column_stack((GRID_LON.ravel()[ocean_indices], GRID_LAT.ravel()[ocean_indices]))
        grid[ocean_indices] = np.asarray(interpolator(target_pts), dtype=np.float64)

    grid_2d = grid.reshape(NY, NX)
    grid_2d[~ocean_grid_mask] = np.nan
    return grid_2d


def to_json_data(grid: np.ndarray, round_digits: int = 2) -> List[Optional[float]]:
    rounded = np.round(grid, round_digits).flatten()
    nan_mask = np.isnan(rounded)
    return [None if is_nan else float(value) for value, is_nan in zip(rounded, nan_mask)]


def build_header(spec: Dict, ref_time: str, forecast_time: int, data_length: int) -> Dict:
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
    raw = get_variable_1d(ds, spec["name"], mt_idx, spec["has_layer"])
    valid_mask: np.ndarray = shared_grid["valid_mask"]
    tri: Delaunay = shared_grid["tri"]

    if raw.ndim != 1 or raw.size != valid_mask.size:
        raise ValueError(f"변수 격자 셀 수 불일치 ({file_path.name} / {spec['name']})")

    values = raw[valid_mask].astype(np.float64)
    if values.size != tri.npoints:
        raise ValueError(f"마스킹 후 유효 값 수 불일치 ({file_path.name} / {spec['name']})")

    nan_count = int((~np.isfinite(values)).sum())
    grid_2d = interpolate_to_ocean_grid(values, shared_grid)
    json_data = to_json_data(grid_2d)

    ocean_grid_mask: np.ndarray = shared_grid["ocean_grid_mask"]
    non_null_count = sum(1 for v in json_data if v is not None)
    """
    print(
        f"    - [{spec['name']}] nan_in_src={nan_count}, "
        f"masked_land={int((~ocean_grid_mask).sum())}, valid_ocean_out={non_null_count}"
    )
    """
    return json_data


# ---------------------------------------------------------------------------
# 파일 / DB 출력 및 검증
# ---------------------------------------------------------------------------

def extract_forecast_hour(file_path: Path) -> Optional[int]:
    match = re.search(r"_f(\d{3})_", file_path.name)
    return int(match.group(1)) if match else None


def collect_rtofs_files(download_dir: str, mode: str) -> Dict[int, Path]:
    files_by_hour: Dict[int, Path] = {}
    for file_path in sorted(Path(download_dir).glob(f"rtofs_glo_*_f???_{mode}.nc")):
        forecast_hour = extract_forecast_hour(file_path)
        if forecast_hour is not None:
            files_by_hour[forecast_hour] = file_path
    return dict(sorted(files_by_hour.items(), key=lambda item: item[0]))


def get_first_rtofs_file(files_by_hour: Dict[int, Path], mode: str) -> Tuple[int, Path]:
    for forecast_hour, nc_file in files_by_hour.items():
        if nc_file.exists():
            return forecast_hour, nc_file
    raise FileNotFoundError(f"fxxx 순서의 첫 번째 {mode}.nc 파일을 찾을 수 없습니다.")


def get_ice_ref_time() -> str:
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_ice_time_str(forecast_hour: int) -> str:
    base_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (base_dt + timedelta(hours=forecast_hour)).strftime("%Y%m%d%H")


def get_prog_ref_time(first_prog_file: Path, first_forecast_hour: int) -> str:
    with xr.open_dataset(first_prog_file, chunks={"MT": 1}) as ds:
        mt_array = pd.to_datetime(ds["MT"].compute().values)
        if mt_array.size == 0:
            raise ValueError(f"기준 prog 파일의 MT가 비어 있습니다: {first_prog_file}")

        dt = pd.to_datetime(mt_array[0]).to_pydatetime()
        if first_forecast_hour != 0:
            dt = dt - timedelta(hours=first_forecast_hour)
        return pd.to_datetime(dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def save_payload(output_dir: str, mode: str, time_str: str, output_type: str, payload: List[Dict]) -> str:
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
        validate_dataset_grid_compatibility(ds, shared_grid, file_path, check_coords=check_coords)

        mt_array = pd.to_datetime(ds["MT"].compute().values)
        if mt_array.size == 0:
            print(f"[스킵] MT가 비어 있습니다: {file_path}")
            return generated_files

        start_idx = max(mt_range[0], 0) if mt_range else 0
        end_idx = min(mt_range[1], mt_array.size - 1) if mt_range else mt_array.size - 1
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
            time_str = (
                get_ice_time_str(forecast_hour)
                if mode == "ice"
                else pd.to_datetime(mt_array[mt_idx]).strftime("%Y%m%d%H")
            )
            print(
                f" MT={mt_idx}/{mt_array.size - 1}, ref_time={ref_time}, "
                f"forecast_time={time_str}, forecast_hour={forecast_hour}"
            )

            for output_type, output_specs in grouped_specs.items():
                payload: List[Dict] = []
                for spec in output_specs:
                    try:
                        data = interpolate_variable(ds, spec, mt_idx, shared_grid, file_path)
                        payload.append({
                            "header": build_header(spec, ref_time, forecast_hour, len(data)),
                            "data": data,
                        })
                    except Exception as error:
                        print(f"[변수 처리 오류] {file_path.name} / {spec['name']}: {error}")
                    finally:
                        gc.collect()

                if not payload:
                    continue

                if conn:
                    record = (time_str, db_mode, output_type, "surface", "level", json.dumps(payload))
                    batch_data.append(record)
                    if forecast_hour == 0:
                        current_data.append(record)
                else:
                    generated_files.append(
                        save_payload(output_dir, mode, time_str, output_type, payload)
                    )

            print(f"[MT 완료] 소요 시간: {time.time() - t0:.2f}초")
            gc.collect()

    print(f"[{mode} 파일 처리 완료] {file_path.name}")
    return generated_files


def verify_generated_grid_data(
        mode: str,
        output_dir: str,
        generated_files: List[str],
        in_memory_records: Optional[List[Tuple]] = None,
) -> None:
    print(f"\n=== 3. 생성 데이터 좌표 검증 (mode={mode}) ===")
    payloads: List[Tuple[str, List[Dict]]] = []

    if in_memory_records:
        target_mode = "ice" if mode == "ice" else "ocean"
        for forecast_time, rec_mode, rec_type, _, _, grib_json_str in in_memory_records:
            if rec_mode == target_mode:
                payloads.append((f"{forecast_time}:{rec_type}", json.loads(grib_json_str)))
    else:
        for fname in sorted(set(generated_files)):
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
                    failures.append(f"{param_name} / {label} ({grid_lat}, {grid_lon}): 내륙 값 존재 ({val})")
                print(
                    f"  [{'PASS' if val is None else 'FAIL'}] {param_name} | {label} "
                    f"-> grid({grid_lat}, {grid_lon}) idx={flat_idx}: value={val} (expected=None)"
                )
            elif mode == "prog":
                if val is None:
                    failures.append(f"{param_name} / {label} ({grid_lat}, {grid_lon}): prog 해상 값 누락")
                print(
                    f"  [{'PASS' if val is not None else 'FAIL'}] {param_name} | {label} "
                    f"-> grid({grid_lat}, {grid_lon}) idx={flat_idx}: value={val} (expected=non-None)"
                )
            else:
                print(f"  [INFO] {param_name} | {label} -> grid({grid_lat}, {grid_lon}) idx={flat_idx}: value={val}")

    if failures:
        raise AssertionError("생성 데이터 좌표 검증 실패:\n - " + "\n - ".join(failures))

    print("[검증 완료] 대표 내륙/해양 좌표 점검을 통과했습니다.")


def merge_and_overwrite(target_list: List[str], new_list: List[str]) -> List[str]:
    filtered = [item for item in target_list if item not in new_list]
    return filtered + new_list


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

    merged_files = merge_and_overwrite(catalog.get("files", []), sorted(set(generated_files)))
    with open(catalog_path, "w", encoding="utf-8") as file:
        json.dump({"files": merged_files, "total_count": len(merged_files)}, file, ensure_ascii=False, indent=4)

    print(f"[catalog 저장 완료] {catalog_path} / total_count={len(merged_files)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert RTOFS ice.nc or prog.nc files to GRIB2 JSON using a reusable shared_grid file."
    )
    parser.add_argument("--mode", type=str, required=True, choices=["ice", "prog"])
    parser.add_argument("--download_dir", type=str, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--output_dir", type=str, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--shared_grid_path", type=str, default=None)
    parser.add_argument("--topo_path", type=str, default=DEFAULT_TOPO_PATH)
    parser.add_argument("--buffer_meters", type=float, default=DEFAULT_LAND_BUFFER_METERS)
    parser.add_argument("--rebuild_shared_grid", action="store_true")
    parser.add_argument("--check_coords", action="store_true")
    parser.add_argument("--verify_data", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mt_range", type=int, nargs=2, default=None)
    parser.add_argument("--useDb", type=str, default="Y", choices=["Y", "N"])
    parser.add_argument("--db_path", type=str, default=DEFAULT_DB_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    default_grid = DEFAULT_ICE_GRID_PATH if args.mode == "ice" else DEFAULT_PROG_GRID_PATH
    shared_grid_path = args.shared_grid_path or default_grid
    variable_specs = ICE_VARIABLE_SPECS if args.mode == "ice" else PROG_VARIABLE_SPECS

    conn = sqlite3.connect(args.db_path) if args.useDb == "Y" else None
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    files_by_hour = collect_rtofs_files(args.download_dir, args.mode)
    if not files_by_hour:
        raise FileNotFoundError(f"처리할 RTOFS {args.mode}.nc 파일이 없습니다: {args.download_dir}")

    first_hour, first_nc_file = get_first_rtofs_file(files_by_hour, args.mode)
    print(f"[첫 번째 {args.mode}.nc] f{first_hour:03d}: {first_nc_file.name}")

    ref_time = (
        get_ice_ref_time()
        if args.mode == "ice"
        else get_prog_ref_time(first_nc_file, first_hour)
    )

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
        db_mode = "ice" if args.mode == "ice" else "ocean"
        type_prefix = "ice%" if args.mode == "ice" else "prog%"
        cutoff_time = (
            get_ice_time_str(0)
            if args.mode == "ice"
            else pd.to_datetime(ref_time).strftime("%Y%m%d%H")
        )
        conn.cursor().execute(
            "DELETE FROM Earth_Current_Tbl WHERE mode = ? AND type LIKE ? AND forecast_time < ?",
            (db_mode, type_prefix, cutoff_time),
        )
        conn.commit()
        conn.close()
    else:
        update_catalog(args.output_dir, all_generated_files)

    print("[작업 종료]")


if __name__ == "__main__":
    main()
