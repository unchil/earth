from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
import re
import shutil
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
DIR_DOWNLOAD = Path("/Library/WebServer/Documents/data/download")

# RtofsInterpToGribJson2.py의 PROG_VARIABLE_SPECS 구조를 적용한 GFS 변수 스펙
# output_type이 동일한 항목(u, v -> "wind")은 하나의 payload 리스트로 병합됨
GFS_VARIABLE_SPECS: List[Dict] = [
    {
        "name": "u",
        "output_type": "wind",
        "parameterCategory": 2,
        "parameterCategoryName": "Momentum",
        "parameterNumber": 2,
        "parameterNumberName": "U-component_of_wind",
        "parameterUnit": "m.s-1",
    },
    {
        "name": "v",
        "output_type": "wind",
        "parameterCategory": 2,
        "parameterCategoryName": "Momentum",
        "parameterNumber": 3,
        "parameterNumberName": "V-component_of_wind",
        "parameterUnit": "m.s-1",
    },
    {
        "name": "t",
        "output_type": "temperature",
        "parameterCategory": 0,
        "parameterCategoryName": "Temperature",
        "parameterNumber": 0,
        "parameterNumberName": "Temperature",
        "parameterUnit": "K",
    },
    {
        "name": "r",
        "output_type": "relative_humidity",
        "parameterCategory": 1,
        "parameterCategoryName": "Moisture",
        "parameterNumber": 1,
        "parameterNumberName": "Relative_humidity",
        "parameterUnit": "%",
    },
]


def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"URL 요청 실패 ({url}): {e}")
        return f"URL 요청 실패 ({url}): {e}"


# 서버 에러 또는 데이터 미존재 시 응답 내용에 포함될 수 있는 패턴 목록
INVALID_RESPONSE_PATTERNS = [
    "data file is not present".encode("utf-8"),
    "404 Not Found".encode("utf-8"),
    "An error occurred".encode("utf-8"),
    "File not found".encode("utf-8"),
    "<html".encode("utf-8"),  # GRIB2 binary 파일 대신 HTML 에러 페이지가 리턴된 경우
]


def is_invalid_response(content: bytes) -> bool:
    return any(pattern.lower() in content.lower() for pattern in INVALID_RESPONSE_PATTERNS)


def get_last_data(max_retry: int, date_str: str) -> tuple[str, str, str]:
    retry_count = 1
    if retry_count > max_retry:
        print(
            f" 오류: 최대 재시도 횟수({max_retry})를 초과했습니다."
            " 과거 날짜에도 데이터가 존재하지 않습니다. 프로그램을 종료합니다."
        )
        sys.exit(0)

    print(
        f"-> [{retry_count}/{max_retry}] : 서버에 데이터 파일이 없습니다."
        " 하루 이전 날짜의 마지막 사이클로 전환합니다."
    )

    current_dt = datetime.strptime(date_str, "%Y%m%d")
    prev_dt = current_dt - timedelta(days=1)
    date_str = prev_dt.strftime("%Y%m%d")
    latest_dir = f"gfs.{date_str}"

    cycle_html = fetch_url(f"{BASE_URL}?dir=/{latest_dir}")
    cycles = re.findall(r">(\d{2})</a>", cycle_html)
    latest_cycle = max(cycles, key=int) if cycles else "18"

    print(f"-> 전환된 날짜: {date_str} ({latest_dir}), 사이클: {latest_cycle}")
    return (date_str, latest_dir, latest_cycle)


def build_gfs_header(
        spec: Dict,
        ds: xr.Dataset,
        var_name: str,
        center_name: str,
        ref_time: str,
        forecast_time: int,
        surface1_value: float,
) -> Dict:
    da = ds[var_name]
    shape = da.shape
    number_points = da.values.flatten().size
    la2 = float(da.latitude.values.min())
    la1 = float(da.latitude.values.max())
    lo1 = float(da.longitude.values.min())
    lo2 = float(da.longitude.values.max())

    return {
        "discipline": 0,
        "disciplineName": "Meteorological products",
        "center": 7,
        "centerName": center_name,
        "refTime": ref_time,
        "forecastTime": forecast_time,
        "parameterCategory": spec["parameterCategory"],
        "parameterCategoryName": spec["parameterCategoryName"],
        "parameterNumber": spec["parameterNumber"],
        "parameterNumberName": spec["parameterNumberName"],
        "parameterUnit": spec["parameterUnit"],
        "surface1Type": 100,
        "surface1TypeName": "Isobaric surface",
        "surface1Value": surface1_value,
        "numberPoints": number_points,
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
    }


################################################
##################  main  ######################
################################################
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--maxH", type=int, default=72, help="Maximum forecast duration (default: 72)")
    parser.add_argument("--retry", type=int, default=3, help="Maximum number of retries (default: 3)")
    parser.add_argument("--useDb", type=str, default="Y", choices=["Y", "N"], help="Whether a database insert occurred (default: Y)")
    parser.add_argument("--type", type=str, default="all", choices=["all", "download", "decoding"], help="Processing Option (default: all)")

    args = parser.parse_args()

    max_hour = args.maxH
    max_retry = args.retry
    conn = None

    print(f"사용법: python {sys.argv[0]} [--maxH 최대예보시간] [--retry 최대재시도횟수] [--useDb Y/N] [--type all/download/decoding]")
    print(f"입력된 옵션: maxH[{max_hour}], retry[{max_retry}], useDb[{args.useDb}], type[{args.type}]")

    if args.type != "decoding":
        VARIABLES = ["UGRD", "VGRD", "TMP", "RH"]
        LEVELS = ["1000_mb", "850_mb", "700_mb", "500_mb", "250_mb", "70_mb", "10_mb"]

        print("=== NOMADS 서버에서 최신 날짜 및 사이클 정보 확인 중 ===")

        html_content = fetch_url(BASE_URL)
        match_dir = re.search(r'">(gfs\.\d{8})</a>', html_content)

        if not match_dir:
            print("오류: 최신 날짜 디렉터리를 가져오지 못했습니다.")
            sys.exit(0)
        else:
            latest_dir = match_dir.group(1)
            date_str = latest_dir.split(".")[1]

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

        params_dict = {f"var_{v}": "on" for v in VARIABLES}
        params_dict.update({f"lev_{l}": "on" for l in LEVELS})

        if not DIR_DOWNLOAD.exists():
            DIR_DOWNLOAD.mkdir(parents=True, exist_ok=True)

        i = 0
        while i <= max_hour:
            f_num = f"{i:03d}"
            filename = f"gfs.t{latest_cycle}z.pgrb2.1p00.f{f_num}"

            query_string = urllib.parse.urlencode(params_dict)
            download_url = f"{BASE_URL}?file={filename}&dir={dir_path}&{query_string}"

            print(f"Downloading {filename}...")
            tmp_file = DIR_DOWNLOAD / f"{filename}.tmp"

            try:
                urllib.request.urlretrieve(download_url, tmp_file)
            except Exception as e:
                print(f"다운로드 중 오류 발생: {e}")
                i += 3
                continue

            with open(tmp_file, "rb") as f:
                content = f.read(2048)
                if is_invalid_response(content):
                    tmp_file.unlink()
                    print(f"invalid_response:{tmp_file}")
                    i += 3
                    continue

            final_file = DIR_DOWNLOAD / filename
            if tmp_file.exists():
                shutil.move(tmp_file, final_file)
            i += 3

        print("=== 모든 데이터 다운로드 완료 ===")

    if args.type != "download":
        print("=== 디코딩 시작 ===")

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
        levels = [1000.0, 850.0, 700.0, 500.0, 250.0, 70.0, 10.0]

        for src_file in src_files:
            if not src_file.is_file():
                continue
            filename = src_file.name
            f_num = filename[-3:]
            offset_hours = int(f_num)

            if args.useDb == "Y":
                mode = "air"
                surface = "isobaric"

                for level in levels:
                    with xr.open_dataset(
                            src_file,
                            engine="cfgrib",
                            backend_kwargs={
                                "filter_by_keys": {"typeOfLevel": "isobaricInhPa", "level": level},
                                "indexpath": "",
                            },
                    ) as ds:
                        center_name = ds.attrs.get("GRIB_centreDescription", "US National Weather Service - NCEP")
                        ref_time = pd.to_datetime(ds["time"].values).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        valid_time = pd.to_datetime(ds["valid_time"].values).strftime("%Y%m%d%H")
                        forecast_time = int(ds["step"].values / np.timedelta64(1, "h"))
                        surface1_value = float(ds["isobaricInhPa"].item()) * 100

                        # output_type 기준으로 스펙 그룹화 (u, v -> "wind" 하나로 묶임)
                        grouped_specs: Dict[str, List[Dict]] = {}
                        for spec in GFS_VARIABLE_SPECS:
                            if spec["name"] not in ds.data_vars:
                                print(f"[변수 없음] {filename} ({int(level)}hPa): {spec['name']} 스킵")
                                continue
                            grouped_specs.setdefault(spec["output_type"], []).append(spec)

                        for output_type, output_specs in grouped_specs.items():
                            payload: List[Dict] = []

                            for spec in output_specs:
                                var_name = spec["name"]
                                data = [round(float(x), 5) for x in ds[var_name].values.flatten()]
                                header = build_gfs_header(
                                    spec=spec,
                                    ds=ds,
                                    var_name=var_name,
                                    center_name=center_name,
                                    ref_time=ref_time,
                                    forecast_time=forecast_time,
                                    surface1_value=surface1_value,
                                )
                                payload.append({"header": header, "data": data})

                            if payload:
                                payload_json = json.dumps(payload)
                                level_str = f"{int(level)}hPa"
                                record = (valid_time, mode, output_type, surface, level_str, payload_json)

                                batch_data.append(record)
                                if offset_hours == 0:
                                    current_data.append(record)

                try:
                    query_batch = """INSERT INTO Earth_Tbl (forecast_time, mode, type, surface, level, grib2json)
                                     VALUES (?, ?, ?, ?, ?, ?)
                                         ON CONFLICT(forecast_time, mode, type, surface, level) DO UPDATE SET
                        grib2json = excluded.grib2json"""
                    cursor.executemany(query_batch, batch_data)
                    conn.commit()

                    query_current = """INSERT INTO Earth_Current_Tbl (forecast_time, mode, type, surface, level, grib2json)
                                       VALUES (?, ?, ?, ?, ?, ?)
                                           ON CONFLICT(forecast_time, mode, type, surface, level) DO UPDATE SET
                        grib2json = excluded.grib2json"""
                    cursor.executemany(query_current, current_data)
                    conn.commit()

                    print(f">> {filename}: 데이터가 Insert 되었습니다.")
                except Exception as e:
                    print(f"데이터 Insert 중 오류 발생: {e}")

        if conn:
            print(f">> Earth_Tbl:{len(batch_data)}, Earth_Current_Tbl:{len(current_data)} 데이터가 일괄 삽입되었습니다.")
            conn.close()

        print("=== 모든 데이터 디코딩 완료 ===")


if __name__ == "__main__":
    main()