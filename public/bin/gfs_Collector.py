from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import ssl
import sqlite3
import json
import argparse
import xarray as xr
import pandas as pd
import numpy as np

# SSL 인증서 검증 비활성화 설정 추가
ssl._create_default_https_context = ssl._create_unverified_context

BASE_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
DIR_DOWNLOAD = Path("../download")

def get_category_name(name: str) -> str:
    category_map = {
        "u": "wind",
        "v": "wind",
        "t": "temperature",
        "r": "relative_humidity",
        "siconc": "ice_concentration"
    }
    return category_map.get(name, "Unknown category")


def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"URL 요청 실패 ({url}): {e}")
        return f"URL 요청 실패 ({url}): {e}"


# 서버 에러 또는 데이터 미존재 시 응답 내용에 포함될 수 있는 패턴 목록 (추가 예외사항 발생 시 여기에 추가)
INVALID_RESPONSE_PATTERNS = [
    "data file is not present".encode("utf-8"),
    "404 Not Found".encode("utf-8"),
    "An error occurred".encode("utf-8"),
    "File not found".encode("utf-8"),
    "<html".encode("utf-8")  # GRIB2 binary 파일 대신 HTML 에러 페이지가 리턴된 경우
]


def is_invalid_response(content: bytes) -> bool:
    #다운로드한 파일 내용에 에러/예외 응답 패턴이 포함되어 있는지 검증합니다.
    result = any( pattern.lower() in content.lower() for pattern in INVALID_RESPONSE_PATTERNS )
    return result


def get_last_data(max_retry:int, date_str:str) -> (str, str, str):

    retry_count += 1
    if retry_count > max_retry:
        print(
            f" 오류: 최대 재시도 횟수({max_retry})를 초과했습니다."
            " 과거 날짜에도 데이터가 존재하지 않습니다. 프로그램을 종료합니다."
        )
        if tmp_file.exists():
            tmp_file.unlink()
        sys.exit(0)

    print(
        f"-> [{retry_count}/{max_retry}] : 서버에 데이터 파일이 없습니다."
        " 하루 이전 날짜의 마지막 사이클로 전환합니다."
    )

    # 하루 전 날짜 계산
    current_dt = datetime.strptime(date_str, "%Y%m%d")
    prev_dt = current_dt - timedelta(days=1)
    date_str = prev_dt.strftime("%Y%m%d")
    latest_dir = f"gfs.{date_str}"

    # 이전 날짜 폴더의 마지막 사이클 재추출
    cycle_html = fetch_url(f"{BASE_URL}?dir=/{latest_dir}")
    cycles = re.findall(r">(\\d{2})</a>", cycle_html)
    latest_cycle = max(cycles, key=int) if cycles else "18"
    dir_path = f"/{latest_dir}/{latest_cycle}/atmos"

    print(f"-> 전환된 날짜: {date_str} ({latest_dir}), 사이클: {latest_cycle}")

    return (date_str, latest_dir, latest_cycle)


################################################
##################  main  ######################
################################################
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--maxH", type=int, nargs=1, default=72, help="Maximum forecast duration (default: 72)")
    parser.add_argument("--retry", type=int, nargs=1, default=3, help="Maximum number of retries (default: 3)")
    parser.add_argument("--useDb", type=str, default="Y",  choices=["Y", "N"], help="Whether a database insert occurred(default: Y")
    parser.add_argument("--type", type=str, default="decoding",  choices=["all", "download", "decoding"], help="Processing Option(default: all")

    args = parser.parse_args()

    max_hour = args.maxH
    max_retry = args.retry
    conn = None

    print(f"사용법: python {sys.argv[0]} [최대예보시간] [최대재시도횟수]  [데이터베이스 사용여부] [작업옵션]")
    print(f"입력된 옵션: maxH[{max_hour}], retry[{max_retry}], useDb[{args.useDb}], type[{args.type}]")


    if(args.type != 'decoding'):
        # NOMADS GFS 1.0도 기본 URL 및 설정

        VARIABLES = ["UGRD", "VGRD", "TMP", "RH", "ICEC"]
        LEVELS = ["1000_mb", "850_mb", "700_mb", "500_mb", "250_mb", "70_mb", "10_mb", "surface"]

        print("=== NOMADS 서버에서 최신 날짜 및 사이클 정보 확인 중 ===")

        # 1. 최신 날짜 디렉터리 추출
        html_content = fetch_url(BASE_URL)
        match_dir = re.search(r'">(gfs\.\d{8})</a>', html_content)

        if not match_dir:
            print("오류: 최신 날짜 디렉터리를 가져오지 못했습니다.")
            sys.exit(0)
        else:
            latest_dir = match_dir.group(1)
            date_str = latest_dir.split(".")[1]

        # 2. 최신 사이클 추출
        cycle_html = fetch_url(f"{BASE_URL}?dir=/{latest_dir}")
        cycles = re.findall(r">(\d{2})</a>", cycle_html)

        if cycles:
            latest_cycle = max(cycles, key=int)
        else:
            print("오류: 최신 사이클을 가져오지 못했습니다.")
            (date_str, latest_dir, latest_cycle) = get_last_data(max_retry, date_str)


        print(f"-> 감지된 최신 날짜: {date_str} ({latest_dir})")
        print(f"-> 감지된 가장 마지막 사이클: {latest_cycle}")
        print("=== 다운로드 시작 ===")

        dir_path = f"/{latest_dir}/{latest_cycle}/atmos"

        # 쿼리 파라미터 생성
        params_dict = {f"var_{v}": "on" for v in VARIABLES}
        params_dict.update({f"lev_{l}": "on" for l in LEVELS})

        if not DIR_DOWNLOAD.exists():
            DIR_DOWNLOAD.mkdir(parents=True, exist_ok=True)

        # 3. 데이터 다운로드 루프 (무한 루프 방지용 retry_count 도입)
        i = 0
        while i <= max_hour:

            f_num = f"{i:03d}"
            filename = f"gfs.t{latest_cycle}z.pgrb2.1p00.f{f_num}"

            query_string = urllib.parse.urlencode(params_dict)
            download_url = (
                f"{BASE_URL}?file={filename}&dir={dir_path}&{query_string}"
            )

            print(f"Downloading {filename}...")
            tmp_file = DIR_DOWNLOAD / f"{filename}.tmp"

            try:
                urllib.request.urlretrieve(download_url, tmp_file)
            except Exception as e:
                print(f"다운로드 중 오류 발생: {e}")
                i += 3
                continue

            # 서버가 GRIB2 대신 에러 HTML을 준 경우 확인
            with open(tmp_file, "rb") as f:
                content = f.read(2048)
                if is_invalid_response(content):
                    tmp_file.unlink()
                    print(f"invalid_response:{tmp_file}")
                    i += 3
                    continue

            # 정상 파일 이동
            final_file = DIR_DOWNLOAD / filename
            if tmp_file.exists():
                shutil.move(tmp_file, final_file)
            i += 3

        print("=== 모든 데이터 다운로드 완료 ===")


    if(args.type != 'download'):

        print("=== 디코딩 시작 ===")

        categoryNames = [ "wind", "t", "r", "siconc"]

        src_files = sorted(DIR_DOWNLOAD.glob("gfs.t*z.pgrb2.1p00.f*"))

        if not src_files:
            print(f"정보: {DIR_DOWNLOAD} 에 디코딩할 GRIB2 파일이 없습니다. 종료합니다.")
            sys.exit(0)

        if args.useDb == "Y":
            conn = sqlite3.connect('/Users/unchil/AndroidStudioProjects/OceanWaterInfo/oceanwater.sqlite')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM Earth_Current_Tbl WHERE mode = 'air'")
            conn.commit()


        batch_data = []
        current_data = []

        for src_file in src_files:
            if not src_file.is_file():
                continue
            filename = src_file.name
            f_num = filename[-3:]
            offset_hours = int(f_num)

            if args.useDb == "Y":


                for category in categoryNames:
                    if category == "siconc":
                        mode = 'ocean'
                        surface = 'surface'
                        level = 'level'

                        ds = xr.open_dataset(
                            src_file,
                            engine="cfgrib",
                            backend_kwargs={  "filter_by_keys": {"typeOfLevel": "surface"},  "indexpath": "", }
                        )

                        centerName = ds.attrs['GRIB_centreDescription']
                        refTime = pd.to_datetime(ds["time"].values ).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                        validTime = pd.to_datetime(ds["valid_time"].values ).strftime('%Y%m%d%H')
                        forecastTime = int(f"{int(ds['step'].values / np.timedelta64(1, 'h')):02d}")
                        surface1Value = ds.get('surface').item()

                        shape = ds.data_vars[category].shape
                        numberPoints = ds.data_vars[category].values.flatten().size
                        la2 = float(ds[category].latitude.values.min())
                        la1 = float(ds[category].latitude.values.max())
                        lo1 = float(ds[category].longitude.values.min())
                        lo2 = float(ds[category].longitude.values.max())
                        data = [ round(float(x), 5) for x in ds[category].values.flatten() ]

                        payload = [{
                            "header": {
                                "center": 7,
                                "centerName": centerName,
                                "refTime": refTime,
                                "forecastTime": forecastTime,
                                "surface1Value": surface1Value,
                                "numberPoints": numberPoints,
                                "winds": "true",
                                "nx": shape[1],
                                "ny": shape[0],
                                "basicAngle": 0,
                                "lo1": lo1,
                                "la1": la1,
                                "lo2": lo2,
                                "la2": la2,
                                "dx": 1.0,
                                "dy": 1.0,
                            },
                            "data": data,
                        }]
                        if(offset_hours == 0):
                            current_data.append( (validTime, mode, get_category_name(category), surface, level, json.dumps(payload)) )
                            batch_data.append( (validTime, mode, get_category_name(category), surface, level, json.dumps(payload)) )
                        else:
                            batch_data.append( (validTime, mode, get_category_name(category), surface, level, json.dumps(payload)) )

                    else:
                        mode = 'air'
                        surface = 'isobaric'
                        levels = [1000.,  850.,  700.,  500.,  250.,   70.,   10.]

                        for level in levels:
                            ds = xr.open_dataset(
                                src_file,
                                engine="cfgrib",
                                backend_kwargs={
                                    "filter_by_keys": {"typeOfLevel": "isobaricInhPa", "level": level},
                                    "indexpath": "",
                                },)

                            centerName = ds.attrs['GRIB_centreDescription']
                            refTime = pd.to_datetime(ds["time"].values ).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                            validTime = pd.to_datetime(ds["valid_time"].values ).strftime('%Y%m%d%H')
                            forecastTime = int(f"{int(ds['step'].values / np.timedelta64(1, 'h')):02d}")
                            surface1Value = float(ds["isobaricInhPa"].item()) * 100


                            if category == "wind":
                                wind_data = []
                                for category in ['u', 'v']:
                                    shape = ds.data_vars[category].shape
                                    numberPoints = ds.data_vars[category].values.flatten().size
                                    la2 = float(ds[category].latitude.values.min())
                                    la1 = float(ds[category].latitude.values.max())
                                    lo1 = float(ds[category].longitude.values.min())
                                    lo2 = float(ds[category].longitude.values.max())
                                    data = [ round(float(x), 5) for x in ds[category].values.flatten() ]
                                    payload = [{
                                        "header": {
                                            "center": 7,
                                            "centerName": centerName,
                                            "refTime": refTime,
                                            "forecastTime": forecastTime,
                                            "surface1Value": surface1Value,
                                            "numberPoints": numberPoints,
                                            "winds": "true",
                                            "nx": shape[1],
                                            "ny": shape[0],
                                            "basicAngle": 0,
                                            "lo1": lo1,
                                            "la1": la1,
                                            "lo2": lo2,
                                            "la2": la2,
                                            "dx": 1.0,
                                            "dy": 1.0,
                                        },
                                        "data": data,
                                    }]
                                    wind_data.extend(payload)

                                if offset_hours == 0:
                                    current_data.append( (validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(wind_data)) )
                                    batch_data.append( (validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(wind_data)) )
                                else:
                                    batch_data.append( (validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(wind_data)) )

                            else:
                                shape = ds.data_vars[category].shape
                                numberPoints = ds.data_vars[category].values.flatten().size
                                la2 = float(ds[category].latitude.values.min())
                                la1 = float(ds[category].latitude.values.max())
                                lo1 = float(ds[category].longitude.values.min())
                                lo2 = float(ds[category].longitude.values.max())
                                data = [ round(float(x), 5) for x in ds[category].values.flatten() ]

                                payload = [{
                                    "header": {
                                        "center": 7,
                                        "centerName": centerName,
                                        "refTime": refTime,
                                        "forecastTime": forecastTime,
                                        "surface1Value": surface1Value,
                                        "numberPoints": numberPoints,
                                        "winds": "true",
                                        "nx": shape[1],
                                        "ny": shape[0],
                                        "basicAngle": 0,
                                        "lo1": lo1,
                                        "la1": la1,
                                        "lo2": lo2,
                                        "la2": la2,
                                        "dx": 1.0,
                                        "dy": 1.0,
                                    },
                                    "data": data,
                                }]

                                if(offset_hours == 0):
                                    current_data.append( ( validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(payload)))
                                    batch_data.append( (validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(payload)) )
                                else:
                                    batch_data.append( (validTime, mode, get_category_name(category), surface, f"{int(level)}hPa", json.dumps(payload)) )


                try:
                    query_batch = '''INSERT INTO Earth_Tbl (forecast_time, mode, type, surface, level, grib2json ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(forecast_time, mode, type, surface, level ) DO UPDATE SET
                        grib2json = excluded.grib2json'''
                    # db insert job
                    cursor.executemany(query_batch, batch_data)
                    conn.commit()

                    query_current = '''INSERT INTO Earth_Current_Tbl (forecast_time, mode, type, surface, level, grib2json ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(forecast_time, mode, type, surface, level ) DO UPDATE SET
                        grib2json = excluded.grib2json '''
                    cursor.executemany(query_current, current_data)
                    conn.commit()

                    print(f">> {filename}: 데이터가 Insert 되었습니다.")
                except Exception as e:
                    print(f"데이터 Insert 중 오류 발생: {e}")


        if conn:
            print(f">> Earth_Tbl:{len(batch_data)}, Earth_Current_Tbl:{len(current_data)} 데이터가 일괄 삽입되었습니다.")
            conn.close()


        # 다운로드 디렉터리 초기화
        """
        if DIR_DOWNLOAD.exists():
            shutil.rmtree(DIR_DOWNLOAD)
            print(f"-> {DIR_DOWNLOAD} 디렉터리를 비웠습니다.")
        """
        print("=== 모든 데이터 디코딩 완료 ===")




if __name__ == "__main__":
    main()




