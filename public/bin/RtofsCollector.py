from bs4 import BeautifulSoup
import gzip
import os
import re
import shutil
import ssl
from urllib.parse import urljoin
import urllib.request

# SSL 인증서 검증 우회 설정 (CERTIFICATE_VERIFY_FAILED 에러 방지)
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_url(url):
    """urllib를 사용하여 웹 페이지 HTML을 가져오는 함수"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            return response.read().decode("utf-8")
    except Exception as e:
        print(f"URL 요청 실패 ({url}): {e}")
        return None


def download_latest_available_rtofs():
    base_url = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/"
    print(f"Connecting to base URL: {base_url}")
    download_dir = "../download"

    # 디렉터리가 없으면 생성
    os.makedirs(download_dir, exist_ok=True)

    html_content = fetch_url(base_url)
    if not html_content:
        print("서버에 접속할 수 없습니다.")
        return

    soup = BeautifulSoup(html_content, "html.parser")

    # rtofs.YYYYMMDD 형태의 디렉토리 목록 수집
    date_dirs = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.search(r"rtofs\.(\d{8})/?", href)
        if match:
            date_str = match.group(1)
            date_dirs.append((date_str, href))

    if not date_dirs:
        print("날짜별 RTOFS 디렉토리를 찾지 못했습니다.")
        return

    # 날짜 기준 내림차순 정렬 (가장 최근 날짜부터 과거 순으로 정렬)
    date_dirs.sort(key=lambda x: x[0], reverse=True)

    target_file_pattern = re.compile(rf"rtofs_glo_uv_\d{{8}}_hcasts\.nc\.gz")

    file_url = None
    file_name = None
    found_date = None

    # 최근 날짜 폴더부터 차례대로 탐색
    for date_str, dir_href in date_dirs:
        target_dir_url = urljoin(base_url, dir_href)
        print(f"\n탐색 중인 날짜 폴더: rtofs.{date_str}")

        dir_html = fetch_url(target_dir_url)
        if not dir_html:
            print(f"-> 폴더 접근 실패, 다음 날짜로 넘어갑니다.")
            continue

        dir_soup = BeautifulSoup(dir_html, "html.parser")

        # 1. 최상위 디렉토리에서 파일 직접 검색
        for a in dir_soup.find_all("a", href=True):
            href = a["href"]
            filename = href.split("/")[-1]
            if target_file_pattern.match(filename):
                file_name = filename
                file_url = urljoin(target_dir_url, href)
                break

        # 2. 만약 최상위 루트에 없다면 하위 디렉토리(예: hcast 등) 내부 탐색
        if not file_url:
            for a in dir_soup.find_all("a", href=True):
                href = a["href"]
                if "hcast" in href and href.endswith("/"):
                    sub_dir_url = urljoin(target_dir_url, href)
                    sub_html = fetch_url(sub_dir_url)
                    if sub_html:
                        sub_soup = BeautifulSoup(sub_html, "html.parser")
                        for sub_a in sub_soup.find_all("a", href=True):
                            sub_href = sub_a["href"]
                            sub_filename = sub_href.split("/")[-1]
                            if target_file_pattern.match(sub_filename):
                                file_name = sub_filename
                                file_url = urljoin(sub_dir_url, sub_href)
                                break
                if file_url:
                    break

        # 파일을 찾았으면 반복문 중단
        if file_url:
            found_date = date_str
            print(f"-> [성공] rtofs.{date_str} 폴더에서 대상 파일 발견!")
            break
        else:
            print(f"-> rtofs.{date_str} 내에 조건에 맞는 파일이 없습니다. 이전 날짜 폴더를 탐색합니다.")

    if not file_url:
        print("\n[오류] 탐색 가능한 모든 날짜 폴더에서 조건에 맞는 파일을 찾지 못했습니다.")
        return

    print(f"\n대상 파일명: {file_name}")
    print(f"다운로드 URL: {file_url}")
    print("다운로드 시작...")

    download_path = os.path.join(download_dir, file_name)

    # urllib.request를 이용한 대용량 파일 스트리밍 다운로드
    try:
        req = urllib.request.Request(file_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response, open(download_path, "wb") as f:
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        print(f"파일 다운로드 중 오류 발생: {e}")
        return

    print(f"다운로드 완료: {os.path.abspath(download_path)}")

    # 2. 다운로드된 .gz 파일 자동 압축 해제 (.nc 파일 추출)
    extracted_file_name = file_name[:-3]  # 뒤의 '.gz' 확장자 제거
    print(f"압축 해제 중: {file_name} -> {extracted_file_name}")

    extracted_file_path = os.path.join(download_dir, extracted_file_name)
    try:
        with gzip.open(download_path, "rb") as f_in:
            with open(extracted_file_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    except Exception as e:
        print(f"압축 해제 중 오류 발생: {e}")
        return

    # 3. 원본 .gz 파일 삭제
    os.remove(download_path)
    print(f"압축 해제 및 정리가 완료되었습니다! 최종 파일: {os.path.abspath(extracted_file_path)}")


if __name__ == "__main__":
    download_latest_available_rtofs()