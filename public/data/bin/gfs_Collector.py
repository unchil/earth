from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import ssl


# SSL 인증서 검증 비활성화 설정 추가
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"URL 요청 실패 ({url}): {e}")
        return f"URL 요청 실패 ({url}): {e}"


def main():
    # 사용법: python gfs_Collector_3.py [최대예보시간(기본 72)] [최대재시도횟수(기본 3)]
    max_hour = 72
    max_retry = 3

    if len(sys.argv) > 1:
        try:
            max_hour = int(sys.argv[1])
        except ValueError:
            print(f"사용법: python {sys.argv[0]} [최대예보시간] [최대재시도횟수]")
            sys.exit(1)

    if len(sys.argv) > 2:
        try:
            max_retry = int(sys.argv[2])
        except ValueError:
            print(f"사용법: python {sys.argv[0]} [최대예보시간] [최대재시도횟수]")
            sys.exit(1)

    print(f"=== 입력된 최대예보시간: {max_hour}, 최대재시도횟수: {max_retry} ===")

    # NOMADS GFS 1.0도 기본 URL 및 설정
    BASE_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
    DIR_DOWNLOAD = Path("../download")

    VARIABLES = ["UGRD", "VGRD", "TMP", "RH"]
    LEVELS = ["1000_mb", "850_mb", "700_mb", "500_mb", "250_mb", "70_mb", "10_mb"]

    print("=== NOMADS 서버에서 최신 날짜 및 사이클 정보 확인 중 ===")

    # 1. 최신 날짜 디렉터리 추출
    html_content = fetch_url(BASE_URL)
    match_dir = re.search(r'">(gfs\.\d{8})</a>', html_content)

    if not match_dir:
        print("오류: 최신 날짜 디렉터리를 가져오지 못했습니다. 수동 설정으로 전환합니다.")
        date_str = datetime.utcnow().strftime("%Y%m%d")
        latest_dir = f"gfs.{date_str}"
    else:
        latest_dir = match_dir.group(1)
        date_str = latest_dir.split(".")[1]

    # 2. 최신 사이클 추출
    cycle_html = fetch_url(f"{BASE_URL}?dir=/{latest_dir}")
    cycles = re.findall(r">(\d{2})</a>", cycle_html)
    if cycles:
        latest_cycle = max(cycles, key=int)
    else:
        print("오류: 최신 사이클을 가져오지 못했습니다. 수동 설정으로 전환합니다.")
        latest_cycle = "18"

    print(f"-> 감지된 최신 날짜: {date_str} ({latest_dir})")
    print(f"-> 감지된 가장 마지막 사이클: {latest_cycle}")
    print("=== 다운로드 시작 ===")

    dir_path = f"/{latest_dir}/{latest_cycle}/atmos"

    # 쿼리 파라미터 생성
    params_dict = {}
    for v in VARIABLES:
        params_dict[f"var_{v}"] = "on"
    for l in LEVELS:
        params_dict[f"lev_{l}"] = "on"

    # 다운로드 디렉터리 초기화
    if DIR_DOWNLOAD.exists():
        shutil.rmtree(DIR_DOWNLOAD)
    DIR_DOWNLOAD.mkdir(parents=True, exist_ok=True)
    print(f"-> {DIR_DOWNLOAD} 디렉터리를 비웠습니다.")

    # 3. 데이터 다운로드 루프 (무한 루프 방지용 retry_count 도입)
    i = 0
    retry_count = 0

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

        if b">data file is not present" in content:
            if i == 0:
                # ── [무한 루프 방지 검증 로직] ──
                retry_count += 1
                if retry_count > max_retry:
                    print(
                        f" 오류: 최대 재시도 횟수({max_retry})를 초과했습니다."
                        " 과거 날짜에도 데이터가 존재하지 않습니다. 프로그램을 종료합니다."
                    )
                    if tmp_file.exists():
                        tmp_file.unlink()
                    sys.exit(1)

                print(
                    f"-> [{retry_count}/{max_retry}] {filename}: 서버에 데이터 파일이 없습니다."
                    " 하루 이전 날짜의 마지막 사이클로 전환합니다."
                )

                # 하루 전 날짜 계산
                current_dt = datetime.strptime(date_str, "%Y%m%d")
                prev_dt = current_dt - timedelta(days=1)
                date_str = prev_dt.strftime("%Y%m%d")
                latest_dir = f"gfs.{date_str}"

                # 이전 날짜 폴더의 마지막 사이클 재추출
                cycle_html = fetch_url(f"{BASE_URL}?dir=/{latest_dir}")
                cycles = re.findall(r">(\d{2})</a>", cycle_html)
                latest_cycle = max(cycles, key=int) if cycles else "18"
                dir_path = f"/{latest_dir}/{latest_cycle}/atmos"

                print(f"-> 전환된 날짜: {date_str} ({latest_dir}), 사이클: {latest_cycle}")

                # 다운로드 폴더 재초기화
                shutil.rmtree(DIR_DOWNLOAD)
                DIR_DOWNLOAD.mkdir(parents=True, exist_ok=True)

                # f000부터 다시 시도하기 위해 continue (i는 0으로 유지됨)
                continue
            else:
                print(f"-> {filename}: 서버에 데이터 파일이 아직 없습니다. 스킵합니다.")
                if tmp_file.exists():
                    tmp_file.unlink()
                i += 3
                continue

        # 정상 파일인 경우 retry_count 초기화 (성공적인 다운로드 진행 시)
        retry_count = 0

        # 정상 파일 이동
        final_file = DIR_DOWNLOAD / filename
        if tmp_file.exists():
            shutil.move(tmp_file, final_file)

        i += 3

    print("=== 모든 데이터 다운로드 완료 ===")

    # 4. GRIB2 디코딩 단계
    DIR_ATMOS = Path("../weather")

    LAYERS = [
        ("isobaric", "1000hPa", "100", "100000"),
        ("isobaric", "850hPa", "100", "85000"),
        ("isobaric", "700hPa", "100", "70000"),
        ("isobaric", "500hPa", "100", "50000"),
        ("isobaric", "250hPa", "100", "25000"),
        ("isobaric", "70hPa", "100", "7000"),
        ("isobaric", "10hPa", "100", "1000"),
    ]

    PARAMETERS = [
        ("2", "Momentum", "wind", "wind"),
        ("0", "Temperature", "0", "temperature"),
        ("1", "Moisture", "1", "relative_humidity"),
    ]

    print("=== grib2json 디코딩 시작 ===")

    src_files = sorted(DIR_DOWNLOAD.glob("gfs.t*z.pgrb2.1p00.f*"))
    if not src_files:
        print(f"정보: {DIR_DOWNLOAD} 에 디코딩할 GRIB2 파일이 없습니다. 종료합니다.")
        sys.exit(1)

    # 기준 시각 (UTC)
    base_dt = datetime.strptime(f"{date_str}{latest_cycle}", "%Y%m%d%H")

    for src_file in src_files:
        if not src_file.is_file():
            continue
        filename = src_file.name
        f_num = filename[-3:]
        offset_hours = int(f_num)

        # offset_hours를 더한 대상 시각 계산
        target_dt = base_dt + timedelta(hours=offset_hours)
        target_date_dir = target_dt.strftime("%Y/%m/%d")
        target_dir = DIR_ATMOS / target_date_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = target_dt.strftime("%H%M")

        for surface, level, fs, fv in LAYERS:
            for fc, fc_name, fp, var in PARAMETERS:
                target_filename = f"{prefix}-{var}-{surface}-{level}-gfs-1.0.json"
                target_file_path = target_dir / target_filename

                print(
                    f"Decoding {filename} -> {target_date_dir}/{target_filename} (surface:"
                    f" {fs}, value: {fv})..."
                )

                # grib2json 명령어 실행
                cmd = [
                    "grib2json",
                    "-d",
                    "-n",
                    "-c",
                    "--fc",
                    fc,
                    "--fp",
                    fp,
                    "--fs",
                    fs,
                    "--fv",
                    fv,
                    "-o",
                    str(target_file_path),
                    str(src_file),
                ]

                try:
                    subprocess.run(cmd, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"grib2json 실행 오류: {e}")
                    continue
                except FileNotFoundError as e:
                    print(f" 오류: grib2json 파일을 찾을 수 없습니다. 프로그램을 종료합니다. 경로: {e.filename} ")
                    sys.exit(1)

                # 1000hPa 파일 복사 처리
                if level == "1000hPa":
                    surface_level_path = (
                            target_dir / f"{prefix}-{var}-surface-level-gfs-1.0.json"
                    )
                    shutil.copy(target_file_path, surface_level_path)

                # f_num이 "000"일 경우 current 디렉터리에도 복사
                if f_num == "000":
                    current_dir = DIR_ATMOS / "current"
                    current_dir.mkdir(parents=True, exist_ok=True)
                    current_file_path = (
                            current_dir / f"current-{var}-{surface}-{level}-gfs-1.0.json"
                    )
                    shutil.copy(target_file_path, current_file_path)

                    if level == "1000hPa":
                        current_surface_path = (
                                current_dir / f"current-{var}-surface-level-gfs-1.0.json"
                        )
                        shutil.copy(target_file_path, current_surface_path)

    print("=== 모든 데이터 디코딩 완료 ===")


if __name__ == "__main__":
    main()
