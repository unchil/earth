import os
import re
import gzip
import shutil
import argparse
import xarray as xr
import numpy as np
import json
import gc
from scipy.interpolate import LinearNDInterpolator
import pandas as pd
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

def download_and_extract_rtofs():

    """NOMADS 서버에서 가장 최신 가용한 rtofs_glo_uv 파일(.nc.gz)을 다운로드하고 압축 해제 후 경로 반환"""
    base_url = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/"
    print(f"Connecting to base URL: {base_url}")

    response = requests.get(base_url)
    if response.status_code != 200:
        print(f"서버에 접속할 수 없습니다. 상태 코드: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    date_dirs = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        match = re.search(r'rtofs\.(\d{8})/?', href)
        if match:
            date_str = match.group(1)
            date_dirs.append((date_str, href))

    if not date_dirs:
        print("날짜별 RTOFS 디렉토리를 찾지 못했습니다.")
        return None

    # 날짜 기준 내림차순 정렬 (최신순)
    date_dirs.sort(key=lambda x: x[0], reverse=True)
    target_file_pattern = re.compile(rf'rtofs_glo_uv_\d{{8}}_hcasts\.nc\.gz')

    file_url = None
    file_name = None

    for date_str, dir_href in date_dirs:
        target_dir_url = urljoin(base_url, dir_href)
        print(f"탐색 중인 날짜 폴더: rtofs.{date_str}")

        dir_response = requests.get(target_dir_url)
        if dir_response.status_code != 200:
            print(f"-> 폴더 접근 실패, 다음 날짜로 넘어갑니다.")
            continue

        dir_soup = BeautifulSoup(dir_response.text, 'html.parser')

        # 1. 최상위 디렉토리 검색
        for a in dir_soup.find_all('a', href=True):
            href = a['href']
            filename = href.split('/')[-1]
            if target_file_pattern.match(filename):
                file_name = filename
                file_url = urljoin(target_dir_url, href)
                break

        # 2. 하위 디렉토리(hcast 등) 검색
        if not file_url:
            for a in dir_soup.find_all('a', href=True):
                href = a['href']
                if 'hcast' in href and href.endswith('/'):
                    sub_dir_url = urljoin(target_dir_url, href)
                    sub_res = requests.get(sub_dir_url)
                    if sub_res.status_code == 200:
                        sub_soup = BeautifulSoup(sub_res.text, 'html.parser')
                        for sub_a in sub_soup.find_all('a', href=True):
                            sub_filename = sub_a['href'].split('/')[-1]
                            if target_file_pattern.match(sub_filename):
                                file_name = sub_filename
                                file_url = urljoin(sub_dir_url, sub_a['href'])
                                break
                if file_url:
                    break

        if file_url:
            print(f"-> [성공] rtofs.{date_str} 폴더에서 대상 파일 발견!")
            break
        else:
            print(f"-> rtofs.{date_str} 내에 파일이 없음. 이전 날짜 폴더 탐색.")

    if not file_url:
        print("[오류] 탐색 가능한 모든 날짜 폴더에서 파일을 찾지 못했습니다.")
        return None

    print(f"대상 파일명: {file_name}")
    print(f"다운로드 URL: {file_url}")
    print("다운로드 시작...")

    t0 = time.time()
    with requests.get(file_url, stream=True) as r:
        r.raise_for_status()
        with open(file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    print(f"    [벤치마크] 다운로드 완료: {os.path.abspath(file_name)}: {time.time() - t0:.4f}초")

    # 압축 해제 (.nc.gz -> .nc)
    extracted_file_name = file_name[:-3]
    print(f"압축 해제 중: {file_name} -> {extracted_file_name}")

    with gzip.open(file_name, 'rb') as f_in:
        with open(extracted_file_name, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    os.remove(file_name)

    return extracted_file_name


def process_file_with_reused_grid(
        file_path,
        mt_indices,
        output_dir):

    func_start_time = time.time()
    """
    하나의 파일에 대해 LinearNDInterpolator 구조를 최초 1회만 빌드하고,
    지정된 MT 인덱스 배열을 순차적으로 값만 변경하여 메모리 세이프하게 처리하는 함수
    """

    base_name = os.path.splitext(os.path.basename(file_path))[0]
    print(f"파일 처리 시작: [{base_name}]")
    generated_files = []

    with xr.open_dataset(file_path, chunks={"MT": 1}) as ds:
        #  정규 격자 정의
        target_lons = np.arange(0, 360, 1.0)
        target_lats = np.arange(90, -91, -1.0)
        grid_lon, grid_lat = np.meshgrid(target_lons, target_lats)
        ny, nx = grid_lon.shape

        mt_array = pd.to_datetime(ds['MT'].values)

        for mt_idx in mt_indices:
            if mt_idx < 0 or mt_idx >= mt_array.size:
                raise ValueError(f"인덱스 {mt_idx}는 범위를 벗어났습니다. (최대 인덱스: {mt_array.size - 1})")

        lat_2d = ds['Latitude'].compute().values.flatten()
        # [수정] 경도 값이 360도를 초과하는 경우 Modulo 연산으로 정규화 적용 후 values 추출
        lon_2d = ds['Longitude'].compute()
        lon_normalized = xr.where(
            lon_2d >= 360, lon_2d % 360, lon_2d
        )
        lon_2d = lon_normalized.values.flatten()

        first_valid_idx = 0
        u_sample = ds['u_velocity'].isel(MT=first_valid_idx, Layer=0).compute().values.flatten()
        valid_mask = ~np.isnan(u_sample) & ~np.isnan(lat_2d) & ~np.isnan(lon_2d)
        points = np.column_stack((lon_2d[valid_mask], lat_2d[valid_mask]))

        if len(points) == 0:
            print(f"[오류] {base_name} 파일에 유효한 해양 좌표 데이터가 없습니다.")
            return generated_files

        t0 = time.time()
        u_vals_sample = u_sample[valid_mask].astype(np.float64)
        interp_u = LinearNDInterpolator(points, u_vals_sample)
        v_sample = ds['v_velocity'].isel(MT=first_valid_idx, Layer=0).compute().values.flatten()
        v_vals_sample = v_sample[valid_mask].astype(np.float64)
        interp_v = LinearNDInterpolator(points, v_vals_sample)
        print(f" 최초 삼각화 격자 기하 구조 빌드 소요 시간: {time.time() - t0:.4f}초 (총 {len(points)} 포인트)")


        refTime = pd.to_datetime(mt_array[0]).strftime('%Y-%m-%dT%H:%M:%S.000Z')

        for mt_idx in mt_indices:
            t0 = time.time()
            forecastTime = pd.to_datetime(mt_array[mt_idx]).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            time_str = pd.to_datetime(mt_array[mt_idx]).strftime('%Y%m%d%H')

            print(f"[{mt_idx + 1}/{mt_array.size}] MT 시간대 처리 중... ({forecastTime})")

            u_raw = ds['u_velocity'].isel(MT=mt_idx, Layer=0).compute().values.flatten()
            v_raw = ds['v_velocity'].isel(MT=mt_idx, Layer=0).compute().values.flatten()

            u_vals = u_raw[valid_mask].astype(np.float64)
            v_vals = v_raw[valid_mask].astype(np.float64)

            interp_u.values = u_vals.reshape(-1, 1)
            interp_v.values = v_vals.reshape(-1, 1)

            # 실제 격자 보간 연산 수행 시간 (가장 강력한 CPU 병목 후보)
            # 격자 보간 수행
            u_grid = interp_u(grid_lon, grid_lat)
            v_grid = interp_v(grid_lon, grid_lat)

            # NumPy의 벡터화 연산을 이용해 전체 격자 데이터를 한 번에 소수점 2자리 반올림 후 1차원 평탄화
            u_rounded = np.round(u_grid, 2).flatten()
            v_rounded = np.round(v_grid, 2).flatten()
            # 결측치(NaN) 위치를 불리언(Boolean) 마스크로 한 번에 일괄 계산
            u_nan_mask = np.isnan(u_rounded)
            v_nan_mask = np.isnan(v_rounded)
            # 미리 계산된 마스크를 활용하여 빠르게 리스트 변환 (반복문 내 무거운 연산 제거)
            u_flat = [None if isnan else float(val) for val, isnan in zip(u_rounded, u_nan_mask)]
            v_flat = [None if isnan else float(val) for val, isnan in zip(v_rounded, v_nan_mask)]
            print(f"    [벤치마크] 격자 보간 연산 수행 소요 시간: {time.time() - t0:.4f}초")

            t0 = time.time()
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
                        "lo2": float(target_lons[-1]),
                        "la2": float(target_lats[-1]),
                        "dx": 1.0,
                        "dy": 1.0
                    },
                    "data": v_flat
                }
            ]

            try:
                output_filename = f"{time_str}-surface-currents-oscar-1.0.json"
                output_filepath = os.path.join(output_dir, output_filename)
                with open(output_filepath, 'w', encoding='utf-8') as f:
                    json.dump(payload, f)

                print(f"저장 완료! (용량: {os.path.getsize(output_filepath) / (1024*1024):.2f} MB)")
                print(f"    [벤치마크] JSON 파일 저장 소요 시간: {time.time() - t0:.4f}초 (용량: {os.path.getsize(output_filepath) / (1024*1024):.2f} MB)")
                generated_files.append(output_filename)
            except Exception as e:
                print(f"{time_str} 파일 처리 중 에러 발생: {e}")

            # 루프 사이클마다 사용이 끝난 루프 로컬 메모리 비우기 예비 조치
            del u_raw, v_raw, u_grid, v_grid, u_flat, v_flat, u_rounded, v_rounded, u_nan_mask, v_nan_mask
            gc.collect()

    print(f"파일 전체 처리 완료 소요 시간: {time.time() - func_start_time:.4f}초")
    return generated_files


def main():
    parser = argparse.ArgumentParser(description="Automated RTOFS Download, Extract and Convert Pipeline.")
    parser.add_argument("--mt_range", type=int, nargs=2, default=[0, 8], help="Range of MT indices (default: 0 8)")
    parser.add_argument("--output_dir", type=str, default="./oscar", help="Directory to save JSON files (default: ./oscar)")

    args = parser.parse_args()

    start_idx, end_idx = args.mt_range
    if start_idx > end_idx:
        parser.error("오류: --mt_range의 시작 인덱스는 끝 인덱스보다 클 수 없습니다.")

    mt_indices = list(range(start_idx, end_idx + 1))
    os.makedirs(args.output_dir, exist_ok=True)

    # 1. 최신 파일 다운로드 및 압축 해제 실행
    downloaded_nc_file = download_and_extract_rtofs()
    if not downloaded_nc_file:
        print("[종료] 다운로드 가능한 파일이 없어 작업을 중단합니다.")
        return



    print(f"\n격자 보간 고속 처리 시작 (대상 파일: {downloaded_nc_file}, 인덱스 범위: {start_idx} ~ {end_idx})")

    all_generated_files = []
    try:
        file_generated = process_file_with_reused_grid(
            downloaded_nc_file, mt_indices, args.output_dir
        )
        if file_generated:
            all_generated_files.extend(file_generated)
    except Exception as e:
        print(f"컨버팅 중 에러 발생: {e}")
    finally:
        gc.collect()

    # 3. 카탈로그(catalog.json) 생성
    all_generated_files = sorted(list(set(all_generated_files)))
    catalog_path = os.path.join(args.output_dir, "catalog.json")
    catalog_data = {
        "files": all_generated_files,
        "total_count": len(all_generated_files)
    }

    try:
        with open(catalog_path, 'w', encoding='utf-8') as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=4)
        print(f"[카탈로그 저장 완료] 총 {len(all_generated_files)}개 파일 목록이 {catalog_path}에 저장되었습니다.")
    except Exception as e:
        print(f"카탈로그 저장 중 에러 발생: {e}")

    print("[전체 파이프라인 작업 종료]")

if __name__ == "__main__":
    main()

"""
python rtofs_pipeline.py --mt_range 0 4 --output_dir ./custom_output
"""