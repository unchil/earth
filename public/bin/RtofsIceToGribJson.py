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

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
import xarray as xr

DEFAULT_DOWNLOAD_DIR = "/Library/WebServer/Documents/data/download"
DEFAULT_OUTPUT_DIR = "/Library/WebServer/Documents/data/rtofs"
DEFAULT_SHARED_GRID_PATH = "/Library/WebServer/Documents/data/rtofs/shared_grid_ice.pkl"
DEFAULT_DB_PATH = "/Users/unchil/AndroidStudioProjects/OceanWaterInfo/oceanwater.sqlite"
DEFAULT_MAX_DIST_DEG = 2.0

TARGET_LONS = np.arange(0, 360, 1.0)
TARGET_LATS = np.arange(90, -91, -1.0)
GRID_LON, GRID_LAT = np.meshgrid(TARGET_LONS, TARGET_LATS)
GRID_COORDS = np.column_stack((GRID_LON.ravel(), GRID_LAT.ravel()))
NY, NX = GRID_LON.shape

batch_data: List[Tuple] = []
current_data: List[Tuple] = []

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
        "parameterUnit": "K",
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


def extract_forecast_hour(file_path: Path) -> Optional[int]:
    match = re.search(r"_f(\d{3})_", file_path.name)
    if not match:
        return None
    return int(match.group(1))


def collect_ice_files(download_dir: str) -> Dict[int, Path]:
    """download_dir에서 ice.nc 파일만 수집하여 forecast_hour 오름차순으로 반환"""
    download_path = Path(download_dir)
    ice_by_hour: Dict[int, Path] = {}

    for file_path in sorted(download_path.glob("rtofs_glo_*_f???_ice.nc")):
        forecast_hour = extract_forecast_hour(file_path)
        if forecast_hour is None:
            continue
        ice_by_hour[forecast_hour] = file_path

    return dict(sorted(ice_by_hour.items(), key=lambda item: item[0]))


def get_first_ice_file(ice_by_hour: Dict[int, Path]) -> Tuple[int, Path]:
    for forecast_hour, ice_file in ice_by_hour.items():
        if ice_file.exists():
            return forecast_hour, ice_file

    raise FileNotFoundError("fxxx 순서의 첫 번째 ice.nc 파일을 찾을 수 없습니다.")


def normalize_longitude(ds: xr.Dataset) -> np.ndarray:
    lon = ds["Longitude"].compute()
    lon_360 = np.mod(lon.values.flatten(), 360.0)
    return lon_360


def get_variable_2d(
        ds: xr.Dataset,
        variable_name: str,
        mt_idx: int,
        has_layer: bool,
) -> np.ndarray:
    if variable_name not in ds:
        raise KeyError(f"{variable_name} 변수가 파일에 없습니다.")

    da = ds[variable_name]
    if "MT" in da.dims:
        da = da.isel(MT=mt_idx)
    if has_layer and "Layer" in da.dims:
        da = da.isel(Layer=0)

    return da.squeeze().compute().values.flatten()


def build_shared_grid(first_ice_file: Path) -> Dict:
    """기준 ice.nc 파일로부터 유효 격자 마스크, 좌표, cKDTree를 생성합니다."""
    print(f"[공통 해빙 격자 생성 기준 파일] {first_ice_file}")
    t0 = time.time()

    with xr.open_dataset(first_ice_file, chunks={"MT": 1}) as ds:
        lat_1d = ds["Latitude"].compute().values.flatten()
        lon_1d = normalize_longitude(ds)
        n_cells = lat_1d.size

        combined_valid = np.zeros(n_cells, dtype=bool)
        if "ice_coverage" in ds:
            cov_1d = get_variable_2d(ds, "ice_coverage", mt_idx=0, has_layer=False)
            if cov_1d.size == n_cells:
                combined_valid |= np.isfinite(cov_1d) & (cov_1d > 0)

        for spec in ICE_VARIABLE_SPECS:
            var_name = spec["name"]
            if var_name not in ds:
                continue
            val_1d = get_variable_2d(ds, var_name, mt_idx=0, has_layer=bool(spec.get("has_layer", False)))
            if val_1d.size == n_cells:
                combined_valid |= np.isfinite(val_1d)

        valid_mask = combined_valid & np.isfinite(lat_1d) & np.isfinite(lon_1d)
        points = np.column_stack((lon_1d[valid_mask], lat_1d[valid_mask]))

        if len(points) == 0:
            raise ValueError("공통 해빙 격자 생성을 위한 유효 points가 없습니다.")

        print(f"[cKDTree 생성] points={len(points):,} 생성 중...")
        tree = cKDTree(points)

    elapsed = time.time() - t0
    print(f"[공통 해빙 격자 완료] 소요 시간: {elapsed:.2f}초")

    return {
        "tree": tree,
        "valid_mask": valid_mask,
        "points": points,
        "total_cells": int(valid_mask.size),
        "source_file": str(first_ice_file),
    }


def save_shared_grid(shared_grid: Dict, shared_grid_path: str) -> Path:
    grid_file = Path(shared_grid_path)
    grid_file.parent.mkdir(parents=True, exist_ok=True)

    with open(grid_file, "wb") as f:
        pickle.dump(shared_grid, f, protocol=pickle.HIGHEST_PROTOCOL)

    size_mb = grid_file.stat().st_size / 1024 / 1024
    print(f"[shared_grid 저장 완료] {grid_file} ({size_mb:.2f} MB)")
    return grid_file


def load_shared_grid(shared_grid_path: str) -> Dict:
    grid_file = Path(shared_grid_path)
    if not grid_file.exists():
        raise FileNotFoundError(f"shared_grid 파일을 찾을 수 없습니다: {grid_file}")

    with open(grid_file, "rb") as f:
        shared_grid = pickle.load(f)

    required_keys = {"valid_mask", "points"}
    missing = required_keys - set(shared_grid.keys())
    if missing:
        raise ValueError(f"shared_grid 필수 키가 누락되었습니다: {sorted(missing)}")

    valid_mask = np.asarray(shared_grid["valid_mask"], dtype=bool)
    points = np.asarray(shared_grid["points"], dtype=np.float64)

    if valid_mask.ndim != 1:
        raise ValueError(f"valid_mask는 1차원 배열이어야 합니다. 현재 ndim={valid_mask.ndim}")
    if int(valid_mask.sum()) != len(points):
        raise ValueError(
            f"shared_grid 내부 정합성 오류: valid_mask.sum()={int(valid_mask.sum())}, len(points)={len(points)}"
        )

    tree = shared_grid.get("tree")
    if not isinstance(tree, cKDTree) or tree.n != len(points):
        tree = cKDTree(points)
        shared_grid["tree"] = tree

    shared_grid["valid_mask"] = valid_mask
    shared_grid["points"] = points

    print(f"[shared_grid 로드 완료] {grid_file} (points={len(points):,})")
    return shared_grid


def get_or_create_shared_grid(
        shared_grid_path: str,
        first_ice_file: Path,
        rebuild: bool = False,
) -> Dict:
    grid_file = Path(shared_grid_path)
    if grid_file.exists() and not rebuild:
        return load_shared_grid(shared_grid_path)

    shared_grid = build_shared_grid(first_ice_file)
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

    if check_coords and not np.allclose(masked_points, points, atol=coord_atol, equal_nan=True):
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
    points: np.ndarray = shared_grid["points"]

    if raw_1d.ndim != 1:
        raise ValueError(f"{variable_name} 배열은 1차원이어야 합니다. ndim={raw_1d.ndim}")

    if raw_1d.size != valid_mask.size:
        raise ValueError(
            f"변수 격자 셀 수 불일치 ({file_path.name} / {variable_name}): "
            f"raw={raw_1d.size}, valid_mask={valid_mask.size}"
        )

    values = raw_1d[valid_mask].astype(np.float64)
    if values.size != len(points):
        raise ValueError(
            f"마스킹 후 유효 값 수 불일치 ({file_path.name} / {variable_name}): "
            f"values={values.size}, points={len(points)}"
        )

    return values


def to_json_data(grid: np.ndarray, round_digits: int = 2) -> List[Optional[float]]:
    rounded = np.round(grid, round_digits).flatten()
    nan_mask = ~np.isfinite(rounded)
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


def interpolate_ice_variable(
        ds: xr.Dataset,
        spec: Dict,
        mt_idx: int,
        shared_grid: Dict,
        file_path: Path,
        max_dist_deg: float = DEFAULT_MAX_DIST_DEG,
        no_ice_grid_mask: Optional[np.ndarray] = None,
) -> List[Optional[float]]:
    """
    해빙 유효 관측치 기반 최근접 보간을 수행하고, 해빙이 없거나 거리 임계값을 초과하는 격자는 NaN(None)으로 반환합니다.
    """
    raw = get_variable_2d(
        ds=ds,
        variable_name=spec["name"],
        mt_idx=mt_idx,
        has_layer=bool(spec.get("has_layer", False)),
    )

    values = validate_variable_compatibility(
        raw_1d=raw,
        shared_grid=shared_grid,
        file_path=file_path,
        variable_name=spec["name"],
    )

    valid_obs_mask = np.isfinite(values)
    if spec["name"] == "ice_thickness":
        valid_obs_mask &= values > 0.0

    grid = np.full(NY * NX, np.nan, dtype=np.float64)

    if np.any(valid_obs_mask):
        obs_points = shared_grid["points"][valid_obs_mask]
        obs_values = values[valid_obs_mask]

        obs_tree = (
            shared_grid["tree"]
            if np.all(valid_obs_mask)
            else cKDTree(obs_points)
        )
        dists, indices = obs_tree.query(GRID_COORDS, k=1, distance_upper_bound=max_dist_deg)
        in_range = np.isfinite(dists) & (dists <= max_dist_deg) & (indices < len(obs_values))
        grid[in_range] = obs_values[indices[in_range]]

    grid_2d = grid.reshape(NY, NX)
    if no_ice_grid_mask is not None:
        grid_2d[no_ice_grid_mask] = np.nan

    return to_json_data(grid_2d)


def build_no_ice_grid_mask(
        ds: xr.Dataset,
        shared_grid: Dict,
        mt_idx: int,
        max_dist_deg: float = DEFAULT_MAX_DIST_DEG,
) -> Optional[np.ndarray]:
    """
    현재 시점(mt_idx)의 ice_coverage를 기준으로 해빙이 없는 목표 격자(True = 해빙 없음 -> None) 마스크를 생성합니다.
    """
    if "ice_coverage" not in ds:
        return None

    cov_raw = get_variable_2d(ds, "ice_coverage", mt_idx=mt_idx, has_layer=False)
    cov_values = validate_variable_compatibility(
        raw_1d=cov_raw,
        shared_grid=shared_grid,
        file_path=Path(str(shared_grid.get("source_file", "ice.nc"))),
        variable_name="ice_coverage",
    )

    has_ice_obs = np.isfinite(cov_values) & (cov_values > 0.0)
    if not np.any(has_ice_obs):
        return np.ones((NY, NX), dtype=bool)

    ice_points = shared_grid["points"][has_ice_obs]
    ice_tree = cKDTree(ice_points)
    dists, _ = ice_tree.query(GRID_COORDS, k=1, distance_upper_bound=max_dist_deg)
    has_ice_grid = np.isfinite(dists) & (dists <= max_dist_deg)
    return (~has_ice_grid).reshape(NY, NX)


def save_payload(
        output_dir: str,
        time_str: str,
        output_type: str,
        payload: List[Dict],
) -> str:
    output_filename = f"{time_str}-surface-{output_type}-rtofs-1.0.json"
    output_path = Path(output_dir) / output_filename

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"[저장 완료] ice / {output_type}: {output_filename} ({size_mb:.2f} MB)")

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


def process_ice_file(
        file_info: Tuple[Path, str, int],
        shared_grid: Dict,
        output_dir: str,
        mt_range: Optional[Tuple[int, int]] = None,
        check_coords: bool = False,
        max_dist_deg: float = DEFAULT_MAX_DIST_DEG,
        conn: Optional[sqlite3.Connection] = None,
) -> List[str]:
    generated_files: List[str] = []

    file_path, ref_time, forecast_hour = file_info
    time_str = get_time_str(forecast_hour)
    print(
        f"[ice 파일 처리 시작] file={file_path.name}, ref_time={ref_time}, "
        f"forecast_hour={forecast_hour}, time_str={time_str}"
    )

    with xr.open_dataset(file_path, chunks={"MT": 1}) as ds:
        validate_dataset_grid_compatibility(
            ds=ds,
            shared_grid=shared_grid,
            file_path=file_path,
            check_coords=check_coords,
        )

        mt_array = pd.to_datetime(ds["MT"].compute().values)
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
        for spec in ICE_VARIABLE_SPECS:
            if spec["name"] not in ds:
                print(f"[변수 없음] {file_path.name}: {spec['name']} 스킵")
                continue
            grouped_specs.setdefault(spec["output_type"], []).append(spec)

        for mt_idx in range(start_idx, end_idx + 1):
            t0 = time.time()
            no_ice_grid_mask = build_no_ice_grid_mask(
                ds=ds,
                shared_grid=shared_grid,
                mt_idx=mt_idx,
                max_dist_deg=max_dist_deg,
            )

            for output_type, output_specs in grouped_specs.items():
                payload: List[Dict] = []

                for spec in output_specs:
                    try:
                        data = interpolate_ice_variable(
                            ds=ds,
                            spec=spec,
                            mt_idx=mt_idx,
                            shared_grid=shared_grid,
                            file_path=file_path,
                            max_dist_deg=max_dist_deg,
                            no_ice_grid_mask=no_ice_grid_mask,
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
                        record = (time_str, "ice", output_type, "surface", "level", json.dumps(payload))
                        batch_data.append(record)
                        if forecast_hour == 0:
                            current_data.append(record)
                    else:
                        output_filename = save_payload(
                            output_dir=output_dir,
                            time_str=time_str,
                            output_type=output_type,
                            payload=payload,
                        )
                        generated_files.append(output_filename)

            print(f"[MT 완료] 소요 시간: {time.time() - t0:.2f}초")
            gc.collect()

    print(f"[ice 파일 처리 완료] {file_path.name}")
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


def get_ref_time() -> str:
    today_00_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today_00_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def get_time_str(first_forecast_hour: int) -> str:
    today_00_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    forecast_time = today_00_utc + timedelta(hours=first_forecast_hour)
    return forecast_time.strftime("%Y%m%d%H")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert RTOFS ice.nc files to GRIB2 JSON using cKDTree nearest interpolation with no-ice masking."
    )
    parser.add_argument(
        "--download_dir",
        type=str,
        default=DEFAULT_DOWNLOAD_DIR,
        help="RTOFS ice.nc download directory",
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
        default=DEFAULT_SHARED_GRID_PATH,
        help="Path to save/load reusable shared_grid pickle file",
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
        "--max_dist_deg",
        type=float,
        default=DEFAULT_MAX_DIST_DEG,
        help="Maximum nearest-neighbor distance in degrees (default: 2.0)",
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
    conn: Optional[sqlite3.Connection] = None
    if args.useDb == "Y":
        conn = sqlite3.connect(args.db_path)

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    ice_by_hour = collect_ice_files(args.download_dir)
    if not ice_by_hour:
        raise FileNotFoundError(f"처리할 RTOFS ice.nc 파일이 없습니다: {args.download_dir}")

    first_hour, first_ice_file = get_first_ice_file(ice_by_hour)
    print(f"[첫 번째 ice.nc] f{first_hour:03d}: {first_ice_file.name}")

    ref_time = get_ref_time()

    shared_grid = get_or_create_shared_grid(
        shared_grid_path=args.shared_grid_path,
        first_ice_file=first_ice_file,
        rebuild=args.rebuild_shared_grid,
    )

    all_generated_files: List[str] = []

    for forecast_hour, ice_file in ice_by_hour.items():
        print(f"\n========== f{forecast_hour:03d} (ice) 처리 시작 ==========")
        try:
            generated_files = process_ice_file(
                file_info=(ice_file, ref_time, forecast_hour),
                shared_grid=shared_grid,
                output_dir=args.output_dir,
                mt_range=tuple(args.mt_range) if args.mt_range else None,
                check_coords=args.check_coords,
                max_dist_deg=args.max_dist_deg,
                conn=conn,
            )
            all_generated_files.extend(generated_files)
        except Exception as error:
            print(f"[파일 처리 오류] {ice_file}: {error}")
        finally:
            gc.collect()
        print(f"========== f{forecast_hour:03d} (ice) 처리 완료 ==========")

    if conn:
        insert_payload(conn)
        time_str = get_time_str(0)
        conn.cursor().execute(
            "DELETE FROM Earth_Current_Tbl WHERE mode = ? AND type LIKE ? AND forecast_time < ?",
            ("ice", "ice%", time_str),
        )
        conn.commit()
        conn.close()
    else:
        update_catalog(args.output_dir, all_generated_files)

    print("[작업 종료]")


if __name__ == "__main__":
    main()
