import os
import argparse
import subprocess
import glob
import time

def main():
    parser = argparse.ArgumentParser(description="RTOFS Data Collection and Grib JSON Conversion Pipeline CLI")

    # RtofsInterpToGribJson.py와 호환되는 인자 정의
    parser.add_argument("--mt_range", type=int, nargs=2, required=True, help="Range of MT indices specified as start and end (e.g., --mt_range 0 8)")
    parser.add_argument("--output_dir", type=str, required=True, help="Directory to save the processed JSON files")
    parser.add_argument("--converter_script", type=str, default="RtofsInterpToGribJson.py", help="Path to the conversion script (default: RtofsInterpToGribJson.py)")

    args = parser.parse_args()

    download_dir = "../download"

    start_idx, end_idx = args.mt_range
    if start_idx > end_idx:
        parser.error("오류: --mt_range의 시작 인덱스는 끝 인덱스보다 클 수 없습니다.")

    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 60)
    print(" [1단계] RtosfsCollector.py 실행 (최신 RTOFS 파일 다운로드 및 압축 해제)")
    print("=" * 60)

    collector_script = "RtofsCollector.py"
    if not os.path.exists(collector_script):
        print(f"[오류] 수집 스크립트 '{collector_script}'를 찾을 수 없습니다.")
        return

    # 수집 스크립트 실행
    t0 = time.time()
    collect_result = subprocess.run(["python3", collector_script], capture_output=False)

    if collect_result.returncode != 0:
        print("[오류] RtofsCollector.py 실행 중 문제가 발생했습니다.")
        return
    print(f" 수집 단계 완료 소요 시간: {time.time() - t0:.4f}초\n")

    print("=" * 60)
    print(" [2단계] 다운로드된 NetCDF 파일 탐색")
    print("=" * 60)

    # 다운로드 및 압축 해제된 .nc 파일 패턴 탐색 (예: rtofs_glo_uv_YYYYMMDD_hcasts.nc)
    nc_files = glob.glob(f"{download_dir}/rtofs_glo_uv_*_hcasts.nc")
    if not nc_files:
        print("[오류] 다운로드된 NetCDF(.nc) 파일을 찾을 수 없습니다.")
        return

    # 가장 최근에 생성된(다운로드된) .nc 파일 선택
    latest_nc_file = max(nc_files, key=os.path.getctime)
    print(f" 대상 NetCDF 파일 확인: {os.path.abspath(latest_nc_file)}\n")

    print("=" * 60)
    print(" [3단계] 격자 보간 및 GRIB JSON 변환 시작")
    print("=" * 60)

    if not os.path.exists(args.converter_script):
        print(f"[오류] 변환 스크립트 '{args.converter_script}'를 찾을 수 없습니다.")
        return

    # 컨버터 CLI 명령어 구성
    cmd = [
        "python3", args.converter_script,
        "--files", latest_nc_file,
        "--mt_range", str(start_idx), str(end_idx),
        "--output_dir", args.output_dir
    ]

    print(f"실행 명령어: {' '.join(cmd)}\n")

    # 변환 스크립트 실행
    t0 = time.time()
    conv_result = subprocess.run(cmd)

    if conv_result.returncode != 0:
        print("[오류] RtofsInterpToGribJson 실행 중 에러가 발생했습니다.")
    else:
        print("=" * 60)
        print(f" [성공] 모든 파이프라인 작업이 완료되었습니다! (총 소요 시간: {time.time() - t0:.4f}초)")
        print(f" 결과 저장 디렉토리: {os.path.abspath(args.output_dir)}")
        os.remove(latest_nc_file)
        print("=" * 60)

if __name__ == "__main__":
    main()
