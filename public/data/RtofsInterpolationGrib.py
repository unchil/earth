import os
import argparse
import xarray as xr
import numpy as np
import json
from scipy.interpolate import griddata

def main():

    parser = argparse.ArgumentParser(description="Convert .nc files to Grib2 JSON.")
    parser.add_argument("file_path", type=str, help="Path to the input NetCDF file (.nc)")
    parser.add_argument("output_dir", type=str, help="Directory to save the Convert output JSON file")
    parser.add_argument("--mt_index", type=int, default=0, help="MT Index to save the Convert output JSON file")

    args = parser.parse_args()

    print(f"options[file_path:{args.file_path}, output_dir:{args.output_dir}, mt_index:{args.mt_index}]")

    file_path = args.file_path
    output_dir = args.output_dir

    # 출력 디렉토리가 없으면 생성
    os.makedirs(output_dir, exist_ok=True)
    mt_idx = args.mt_index
    base_name = os.path.splitext(os.path.basename(file_path))[0]

    print(f"File currently being worked on:[{base_name}]")

    with xr.open_dataset(file_path) as ds:
        mt_array = ds['MT'][:].values
        # 1. 원본 2D 곡선 격자의 위경도 및 유속 데이터 추출 (첫 번째 시간, 표층 기준)
        if  args.mt_index >= 0 & args.mt_index < mt_array.size:
            print(f"[{ mt_idx + 1 }/{mt_array.size}][{mt_array[mt_idx]}] MT 시간대 처리 중...")
        else :
            print(f"[{args.mt_index}는 {mt_array[0]} 이후의 예측 데이터 시간대에 포함되지 않은 인덱스 입니다.")
            return

        lat_2d = ds['Latitude'].values.flatten()
        lon_2d = ds['Longitude'].values.flatten()
        u_raw = ds['u_velocity'].isel(MT=mt_idx, Layer=0).values.flatten()
        v_raw = ds['v_velocity'].isel(MT=mt_idx, Layer=0).values.flatten()

        # 유효한(NaN이 아닌) 데이터 포인트만 필터링하여 보간 정확도 향상
        valid_mask = ~np.isnan(u_raw) & ~np.isnan(lat_2d) & ~np.isnan(lon_2d)
        points = np.column_stack((lon_2d[valid_mask], lat_2d[valid_mask]))

        u_vals = u_raw[valid_mask]
        v_vals = v_raw[valid_mask]

        # 2. 목표로 하는 1도 간격의 정규 격자(Regular Grid) 정의 (예: 경도 0~359, 위도 90~-90)
        target_lons = np.arange(0, 360, 1.0)
        target_lats = np.arange(90, -91, -1.0) # 북쪽에서 남쪽으로 스캔 (Scan Mode 호환)
        grid_lon, grid_lat = np.meshgrid(target_lons, target_lats)

        ny, nx = grid_lon.shape
        print(f"목표 정규 격자 생성 완료: nx={nx}, ny={ny} (총 {nx * ny} 포인트)")

        # 3. SciPy griddata를 이용해 1도 간격 격자로 보간 (Linear interpolation)
        print("1도 정규 격자로 보간(Interpolation) 수행 중...")
        u_grid = griddata(points, u_vals, (grid_lon, grid_lat), method='linear', fill_value=np.nan)
        v_grid = griddata(points, v_vals, (grid_lon, grid_lat), method='linear', fill_value=np.nan)

        # 4. 1D 평탄화 및 결측치 처리 (소수점 2자리 반올림)
        u_flat = [None if np.isnan(val) else round(float(val), 2) for val in u_grid.flatten()]
        v_flat = [None if np.isnan(val) else round(float(val), 2) for val in v_grid.flatten()]

        # 헤더 정보 설정 (dx=1.0, dy=1.0)
        payload = [
            {
                "header": {
                    "discipline": 10,
                    "disciplineName": "Oceanographic products",
                    "center": -3,
                    "centerName": "Earth & Space Research",
                    "refTime": mt_array[0],
                    "parameterCategory": 1,
                    "parameterCategoryName": "Currents",
                    "parameterNumber": 2,
                    "parameterNumberName": "U_component_of_current",
                    "parameterUnit": "m.s-1",
                    "forecastTime": mt_array[mt_idx],
                    "surface1Type": 160,
                    "surface1TypeName": "Depth below sea level",
                    "surface1Value": 0,
                    "numberPoints": len(u_flat),
                    "nx": nx,
                    "ny": ny,
                    "lo1": float(target_lons[0]),
                    "la1": float(target_lats[0]),
                    "lo2": float(target_lons[-1]),
                    "la2": float(target_lats[-1]),
                    "dx": 1.0,
                    "dy": 1.0
                },
                "data": u_flat
            },
            {
                "header": {
                    "discipline": 10,
                    "disciplineName": "Oceanographic products",
                    "center": -3,
                    "centerName": "Earth & Space Research",
                    "refTime": mt_array[0],
                    "parameterCategory": 1,
                    "parameterCategoryName": "Currents",
                    "parameterNumber": 3,
                    "parameterNumberName": "V_component_of_current",
                    "parameterUnit": "m.s-1",
                    "forecastTime": mt_array[mt_idx],
                    "surface1Type": 160,
                    "surface1TypeName": "Depth below sea level",
                    "surface1Value": 0,
                    "numberPoints": len(v_flat),
                    "nx": nx,
                    "ny": ny,
                    "lo1": float(target_lons[0]),
                    "la1": float(target_lats[0]),
                    "lo2": float(target_lons[-1]),
                    "la2": float(target_lats[-1]),
                    "dx": 1.0,
                    "dy": 1.0
                },
                "data": v_flat
            }
        ]

        output_filename = os.path.join(output_dir,  f"rtofs_glo_uv_gfs_{mt_array[mt_idx]}.json")

        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(payload, f)

        print(f"1도 간격 변환 및 JSON 저장 완료: {output_filename}")

if __name__ == "__main__":
    main()
