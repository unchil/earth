import argparse
from bs4 import BeautifulSoup
import gzip
import os
import re
import shutil
import ssl
import time
from urllib.parse import urljoin
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DOWNLOAD_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "data", "download"))
DEFAULT_FILE_TYPES = ["prog"]
ALLOWED_FILE_TYPES = ["diag", "ice", "prog"]


def create_unverified_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_url(url, timeout=30, retries=2, ssl_context=None):
    """urllib를 사용하여 웹 페이지 HTML을 가져오는 함수"""
    ctx = ssl_context or create_unverified_ssl_context()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"URL 요청 실패 ({url}): {e}")
            return None


def stream_download(file_url, download_path, timeout=60, retries=2, ssl_context=None):
    """urllib.request를 이용한 대용량 파일 스트리밍 다운로드"""
    ctx = ssl_context or create_unverified_ssl_context()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(file_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response, open(download_path, "wb") as f:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            return True
        except Exception as e:
            if os.path.exists(download_path):
                try:
                    os.remove(download_path)
                except OSError:
                    pass
            if attempt < retries:
                print(f"재시도 중 ({attempt + 1}/{retries}) - {file_url}: {e}")
                time.sleep(2 * (attempt + 1))
                continue
            print(f"파일 다운로드 중 오류 발생 ({file_url}): {e}")
            return False


def extract_if_gzipped(download_path, download_dir):
    """필요 시 .gz 압축을 해제하고 원본 .gz 파일을 삭제"""
    if not download_path.endswith(".gz"):
        return download_path

    extracted_file_name = os.path.basename(download_path)[:-3]
    extracted_file_path = os.path.join(download_dir, extracted_file_name)
    print(f"압축 해제 중: {os.path.basename(download_path)} -> {extracted_file_name}")

    try:
        with gzip.open(download_path, "rb") as f_in:
            with open(extracted_file_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(download_path)
        return extracted_file_path
    except Exception as e:
        print(f"압축 해제 중 오류 발생 ({download_path}): {e}")
        for path in (download_path, extracted_file_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return None


def normalize_file_types(file_types):
    """선택된 파일 타입 시퀀스를 검증하고 중복을 제거한 튜플로 반환"""
    if not file_types:
        raise ValueError("최소 하나 이상의 file_types를 지정해야 합니다.")

    normalized = []
    for ftype in file_types:
        if ftype not in ALLOWED_FILE_TYPES:
            raise ValueError(
                f"지원하지 않는 file_type입니다: {ftype} (허용값: {ALLOWED_FILE_TYPES})"
            )
        if ftype not in normalized:
            normalized.append(ftype)

    return tuple(normalized)


def build_target_filenames(
        start_hour=0,
        step_hours=3,
        duration_hours=36,
        file_types=DEFAULT_FILE_TYPES,
):
    """
    start_hour부터 duration_hours 뒤까지 step_hours 간격으로
    선택된 file_types 파일명 목록 생성
    """
    selected_types = normalize_file_types(file_types)
    end_hour = start_hour + duration_hours
    targets = []

    for hour in range(start_hour, end_hour + 1, step_hours):
        prefix = f"rtofs_glo_2ds_f{hour:03d}"
        for ftype in selected_types:
            targets.append(f"{prefix}_{ftype}.nc")

    return targets


def download_rtofs_2ds_range(
        start_hour=0,
        step_hours=3,
        duration_hours=36,
        download_dir=DEFAULT_DOWNLOAD_DIR,
        file_types=DEFAULT_FILE_TYPES,
):
    if start_hour < 0:
        raise ValueError("start_hour는 0 이상이어야 합니다.")
    if step_hours < 1:
        raise ValueError("step_hours는 1 이상이어야 합니다.")
    if duration_hours < 0:
        raise ValueError("duration_hours는 0 이상이어야 합니다.")

    selected_types = normalize_file_types(file_types)

    base_url = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/"
    print(f"Connecting to base URL: {base_url}")
    os.makedirs(download_dir, exist_ok=True)
    ssl_ctx = create_unverified_ssl_context()

    html_content = fetch_url(base_url, ssl_context=ssl_ctx)
    if not html_content:
        print("서버에 접속할 수 없습니다.")
        return

    soup = BeautifulSoup(html_content, "html.parser")

    date_dirs = []
    seen_dates = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.search(r"rtofs\.(\d{8})/?", href)
        if match:
            date_str = match.group(1)
            if date_str not in seen_dates:
                seen_dates.add(date_str)
                date_dirs.append((date_str, href))

    if not date_dirs:
        print("날짜별 RTOFS 디렉토리를 찾지 못했습니다.")
        return

    date_dirs.sort(key=lambda x: x[0], reverse=True)

    target_filenames = build_target_filenames(
        start_hour=start_hour,
        step_hours=step_hours,
        duration_hours=duration_hours,
        file_types=selected_types,
    )
    step_count = ((start_hour + duration_hours - start_hour) // step_hours) + 1
    print(
        f"수집 대상 시각: f{start_hour:03d} ~ f{start_hour + duration_hours:03d} "
        f"({step_hours}시간 간격, 파일 유형={selected_types}, 유형당 {step_count}개 / 총 {len(target_filenames)}개 파일)"
    )

    matched_files = []
    found_date = None

    for date_str, dir_href in date_dirs:
        target_dir_url = urljoin(base_url, dir_href)
        print(f"\n탐색 중인 날짜 폴더: rtofs.{date_str}")

        dir_html = fetch_url(target_dir_url, ssl_context=ssl_ctx)
        if not dir_html:
            print("-> 폴더 접근 실패, 다음 날짜로 넘어갑니다.")
            continue

        dir_soup = BeautifulSoup(dir_html, "html.parser")
        available_links = {}

        for a in dir_soup.find_all("a", href=True):
            href = a["href"]
            filename = href.split("/")[-1]
            clean_name = filename[:-3] if filename.endswith(".gz") else filename
            if clean_name in target_filenames and clean_name not in available_links:
                available_links[clean_name] = (filename, urljoin(target_dir_url, href))

        if len(available_links) == len(target_filenames):
            found_date = date_str
            for name in target_filenames:
                matched_files.append(available_links[name])
            print(f"-> [성공] rtofs.{date_str} 폴더에서 대상 파일 {len(matched_files)}개 전체 발견!")
            break
        else:
            print(
                f"-> rtofs.{date_str} 폴더에 {len(available_links)}/{len(target_filenames)}개만 존재합니다. "
                f"전체 세트가 있는 이전 날짜 폴더를 탐색합니다."
            )
            continue

    if not matched_files:
        print("\n[오류] 탐색 가능한 모든 날짜 폴더에서 전체 대상 파일을 찾지 못했습니다.")
        return

    print(f"\n선택된 날짜 폴더: rtofs.{found_date} (총 {len(matched_files)}개 다운로드 시작)")

    completed_files = []
    for idx, (actual_filename, file_url) in enumerate(matched_files, start=1):
        download_path = os.path.join(download_dir, actual_filename)
        print(f"\n[{idx}/{len(matched_files)}] 다운로드 중: {actual_filename}")
        print(f"URL: {file_url}")

        if not stream_download(file_url, download_path, ssl_context=ssl_ctx):
            continue

        final_path = extract_if_gzipped(download_path, download_dir)
        if final_path:
            completed_files.append(final_path)
            print(f"완료: {os.path.abspath(final_path)}")

    print(f"\n전체 작업 완료: {len(completed_files)}/{len(matched_files)}개 파일 준비됨.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="NOAA RTOFS 2ds 예측 파일 범위 다운로드 스크립트"
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=0,
        help="시작 예보 시각(시간 단위, 기본값: 0 -> f000)",
    )
    parser.add_argument(
        "--step-hours",
        type=int,
        default=3,
        help="예보 시각 간격(시간 단위, 기본값: 3)",
    )
    parser.add_argument(
        "--duration-hours",
        type=int,
        default=72,
        help="시작 시각으로부터 수집할 총 시간 범위(시간 단위, 기본값: 72 -> f072까지)",
    )
    parser.add_argument(
        "--download-dir",
        type=str,
        default=DEFAULT_DOWNLOAD_DIR,
        help=f"파일 저장 경로(기본값: {DEFAULT_DOWNLOAD_DIR})",
    )
    parser.add_argument(
        "--file-types",
        nargs="+",
        choices=ALLOWED_FILE_TYPES,
        default=list(DEFAULT_FILE_TYPES),
        help=f"다운로드할 파일 유형 목록(기본값: {' '.join(DEFAULT_FILE_TYPES)})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    download_rtofs_2ds_range(
        start_hour=args.start_hour,
        step_hours=args.step_hours,
        duration_hours=args.duration_hours,
        download_dir=args.download_dir,
        file_types=args.file_types,
    )