import os
import argparse
import xarray as xr
import numpy as np
import json
import gc
from scipy.interpolate import LinearNDInterpolator
import pandas as pd

def process_file_with_reused_grid(file_path, mt_indices, grid_lon, grid_lat, nx, ny, target_lons, target_lats, output_dir):
    """
    하나의 파일에 대해 LinearNDInterpolator 구조를 최초 1회만 빌드하고,
    지정된 MT 인덱스 배열을 순차적으로 값만 변경하여 메모리 세이프하게 처리하는 함수
    """
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    print(f"파일 처리 시작: [{base_name}]")
    # 이번 파일 처리에서 생성된 파일명들을 담을 로컬 리스트
    generated_files = []

    with xr.open_dataset(file_path) as ds:
        mt_array = ds['MT'][:].values

        for mt_idx in mt_indices:
            if mt_idx < 0 or mt_idx >= mt_array.size:
                raise ValueError(f"인덱스 {mt_idx}는 범위를 벗어났습니다. (최대 인덱스: {mt_array.size - 1}) --mt_range 를 다시 설정해 주세요.")


        # 1. 공통 위경도 2D 격자 데이터 추출 및 평탄화
        lat_2d = ds['Latitude'].values.flatten()
        lon_2d = ds['Longitude'].values.flatten()

        # 2. 고정된 삼각화 격자 구조(Points) 생성을 위해 첫 번째 유효 인덱스 샘플링
        # (첫 번째 타임스텝의 마스크를 기준으로 고정 격자 포인트를 생성합니다)

        first_valid_idx = 0
        u_sample = ds['u_velocity'].isel(MT=first_valid_idx, Layer=0).values.flatten()
        valid_mask = ~np.isnan(u_sample) & ~np.isnan(lat_2d) & ~np.isnan(lon_2d)
        points = np.column_stack((lon_2d[valid_mask], lat_2d[valid_mask]))

        if len(points) == 0:
            print(f"[오류] {base_name} 파일에 유효한 해양 좌표 데이터가 없습니다.")
            return

        # 3. 첫 번째 방법(LinearNDInterpolator) 구조 초기화
        # 임시 값으로 뼈대(Delaunay Triangulation)를 최초 1회만 빌드합니다.
        u_vals_sample = u_sample[valid_mask]
        interp_u = LinearNDInterpolator(points, u_vals_sample)

        # V 컴포넌트도 동일한 좌표계를 사용하여 빌드
        v_sample = ds['v_velocity'].isel(MT=first_valid_idx, Layer=0).values.flatten()
        v_vals_sample = v_sample[valid_mask]
        interp_v = LinearNDInterpolator(points, v_vals_sample)

        print(f"고정 격자 기하 구조 빌드 완료. (총 {len(points)} 포인트 삼각화 완료)")


        refTime = pd.to_datetime(mt_array[0]).strftime('%Y-%m-%dT%H:%M:%S.000Z')

        # 4. 입력받은 MT 인덱스 배열 순회 (값만 주입하여 초고속 보간)
        for mt_idx in mt_indices:
            if mt_idx < 0 or mt_idx >= mt_array.size:
                print(f"[경고] 인덱스 {mt_idx}는 범위를 벗어났습니다. (최대 인덱스: {mt_array.size - 1}) 건너뜁니다.")
                continue

            forecastTime = pd.to_datetime(mt_array[mt_idx]).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            time_str = pd.to_datetime(mt_array[mt_idx]).strftime('%Y%m%d%H')

            print(f"[{mt_idx + 1}/{mt_array.size}] MT 시간대 처리 중... ({forecastTime})")


            # [메모리 최적화] 대용량 배열 전체를 읽지 않고 해당 타임스텝 딱 '한 장'만 디스크에서 로드

            u_raw = ds['u_velocity'].isel(MT=mt_idx, Layer=0).values.flatten()
            v_raw = ds['v_velocity'].isel(MT=mt_idx, Layer=0).values.flatten()

            u_vals = u_raw[valid_mask]
            v_vals = v_raw[valid_mask]

            # [첫 번째 방법의 핵심 최적화] 기하학 구조 재빌드 없이 값(.values)만 업데이트
            interp_u.values = u_vals.reshape(-1, 1)
            interp_v.values = v_vals.reshape(-1, 1)

            # 격자 보간 수행
            u_grid = interp_u(grid_lon, grid_lat)
            v_grid = interp_v(grid_lon, grid_lat)

            # 1D 평탄화 및 결측치 소수점 반올림 처리
            u_flat = [None if np.isnan(val) else round(float(val), 2) for val in u_grid.flatten()]
            v_flat = [None if np.isnan(val) else round(float(val), 2) for val in v_grid.flatten()]

            """
            payload = [
                {
                    "header": {
                        "discipline": 10,
                        "disciplineName": "Oceanographic products",
                        "center": -3,
                        "centerName": "Earth & Space Research",
                        "refTime": refTime,
                        "parameterCategory": 1,
                        "parameterCategoryName": "Currents",
                        "parameterNumber": 2,
                        "parameterNumberName": "U_component_of_current",
                        "parameterUnit": "m.s-1",
                        "forecastTime": forecastTime,
                        "surface1Type": 160,
                        "surface1TypeName": "Depth below sea level",
                        "surface1Value": 0,
                        "nx": nx,
                        "ny": ny,
                        "dx": 1.0,
                        "dy": 1.0
                    }
                }
            ]
            """
            # JSON 구조 정의
            payload = [
                {
                    "header": {
                        "discipline": 10,
                        "disciplineName": "Oceanographic products",
                        "center": -3,
                        "centerName": "Earth & Space Research",
                        "refTime": refTime,
                        "parameterCategory": 1,
                        "parameterCategoryName": "Currents",
                        "parameterNumber": 2,
                        "parameterNumberName": "U_component_of_current",
                        "parameterUnit": "m.s-1",
                        "forecastTime": forecastTime,
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
                        "refTime": refTime,
                        "parameterCategory": 1,
                        "parameterCategoryName": "Currents",
                        "parameterNumber": 3,
                        "parameterNumberName": "V_component_of_current",
                        "parameterUnit": "m.s-1",
                        "forecastTime": forecastTime,
                        "surface1Type": 160,
                        "surface1TypeName": "Depth below sea level",
                        "surface1Value": 0,
                        "numberPoints": len(v_flat),
                        "nx": nx,
                        "ny": ny,
                        "lo1": float(target_lons[0]),
                        "la1": float(target_lats[0]),
                        "lo2": float(target_lats[0]),
                        "la2": float(target_lats[-1]),
                        "dx": 1.0,
                        "dy": 1.0
                    },
                    "data": v_flat
                }
            ]


            try:
                output_filename =  f"{time_str}-surface-currents-oscar-1.0.json"
                output_filepath = os.path.join(output_dir, output_filename)
                with open(output_filepath, 'w', encoding='utf-8') as f:
                    json.dump(payload, f)

                print(f"저장 완료! (용량: {os.path.getsize(output_filepath) / (1024*1024):.2f} MB)")
                # 생성된 파일명을 리스트에 추가 (상대 경로 또는 파일명만 필요시 선택)
                generated_files.append(output_filename)
            except Exception as e:
                print(f"파일 처리 중 에러 발생: {e}")

            # 루프 사이클마다 사용이 끝난 루프 로컬 메모리 비우기 예비 조치
            del u_raw, v_raw, u_grid, v_grid, u_flat, v_flat
            gc.collect()


    return generated_files


def main():
    parser = argparse.ArgumentParser(description="Memory-efficient batch netCDF interpolation by reusing grid structure.")
    # 복수의 파일 경로를 공백으로 구분하여 입력받음
    parser.add_argument("--files", type=str, nargs="+", required=True, help="List of input .nc file paths separated by spaces")
    # [변경됨] --mt_indices 대신 --mt_range를 사용하여 시작과 끝 범위를 정수로 2개 입력받음
    parser.add_argument("--mt_range", type=int, nargs=2, required=True, help="Range of MT indices specified as start and end (e.g., --mt_range 0 24)")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory to save the processed JSON files")

    args = parser.parse_args()

    # 입력받은 시작, 끝 범위를 이용해 실제 순회할 인덱스 리스트 생성 (끝 인덱스 포함)
    start_idx, end_idx = args.mt_range
    if start_idx > end_idx:
        parser.error("오류: --mt_range의 시작 인덱스는 끝 인덱스보다 클 수 없습니다.")

    # range(start, end + 1)을 사용하여 지정한 끝 번호까지 포함하여 처리합니다.
    # 만약 끝 번호 직전까지만 처리하려면 end + 1 대신 end를 사용하세요.
    mt_indices = list(range(start_idx, end_idx + 1))

    os.makedirs(args.output_dir, exist_ok=True)

    # 1도 정규 격자 정보 정의 (모든 파일 전역 공통 격자)
    target_lons = np.arange(0, 360, 1.0)
    target_lats = np.arange(90, -91, -1.0)
    grid_lon, grid_lat = np.meshgrid(target_lons, target_lats)
    ny, nx = grid_lon.shape

    print(f"격자 재사용 고속 처리 시작 (선택 파일 수: {len(args.files)}개, 대상 인덱스 범위: {start_idx} ~ {end_idx}, 총 {len(mt_indices)}개)")

    # 전체 실행 동안 생성된 모든 JSON 파일 이름을 수집할 총괄 리스트
    all_generated_files = []

    # 파일 레벨 순차 대형 루프 (메모리 덤핑 방지)
    for file_path in args.files:
        if not os.path.exists(file_path):
            print(f"[오류] 파일 경로가 잘못되었거나 존재하지 않습니다: {file_path}")
            continue

        try:
            file_generated = process_file_with_reused_grid(
                file_path, mt_indices, grid_lon, grid_lat, nx, ny,
                target_lons, target_lats, args.output_dir
            )
            if file_generated:
                all_generated_files.extend(file_generated)
        except Exception as e:
            print(f"파일 처리 중 에러 발생: {e}")
        finally:
            # 파일 하나 처리가 완전히 끝날 때마다 인스턴스 소멸 및 무조건 가비지 컬렉션 호출
            gc.collect()


    # 중복 파일명 제거 (필요시 정렬 수행)
    all_generated_files = sorted(list(set(all_generated_files)))

    # catalog.json 파일로 overwrite 저장
    catalog_path = os.path.join(args.output_dir, "catalog.json")

    # 저장할 구조 정의 (필요에 따라 딕셔너리 형태로 감싸거나 리스트 형태로 바로 저장 가능합니다)
    catalog_data = {
        "files": all_generated_files,
        "total_count": len(all_generated_files)
    }

    try:
        with open(catalog_path, 'w', encoding='utf-8') as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=4)

        print(f"[카탈로그 저장 완료] 총 {len(all_generated_files)}개의 파일 목록이 {catalog_path} 에 overwrite 되었습니다.")
    except Exception as e:
        print(f"파일 처리 중 에러 발생: {e}")
    finally:
        print("[작업 종료]")

if __name__ == "__main__":
    main()

"""
python RtofsInterpolationToGribJson.py \
--files ./test/file1.nc ./test/file2.nc ./test/file3.nc \
---mt_range 0 8 --output_dir ./oscar
"""
