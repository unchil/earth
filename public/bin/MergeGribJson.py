import os
import glob
import subprocess
import json
import argparse

def main():
    parser = argparse.ArgumentParser(description="Convert .grb2 files to JSON using grib2json and merge them into a single file.")
    parser.add_argument("input_dir", type=str, help="Directory containing input .grb2 files")
    parser.add_argument("output_dir", type=str, help="Directory to save the merged output JSON file")
    parser.add_argument("--output_filename", type=str, default="merged_currents.json", help="Filename of the merged output JSON")

    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir

    # 출력 디렉토리가 없으면 생성
    os.makedirs(output_dir, exist_ok=True)
    output_file_path = os.path.join(output_dir, args.output_filename)

    merged_data = []

    # 1. 입력 디렉토리 내의 모든 .grb2 파일 검색
    grb2_files = glob.glob(os.path.join(input_dir, "*.grb2"))
    print(f"총 {len(grb2_files)}개의 .grb2 파일을 발견했습니다.")

    if len(grb2_files) == 0:
        print("변환할 .grb2 파일이 없습니다.")
        return

    # 2. 각 .grb2 파일을 순회하며 grib2json 변환 수행
    for grb_path in grb2_files:
        base_name = os.path.splitext(os.path.basename(grb_path))[0]
        print(f"\n처리 중인 파일: {base_name}")

        # 임시로 저장할 U 성분, V 성분 JSON 파일명 설정
        u_json_path = os.path.join(input_dir, f"{base_name}_u.json")
        v_json_path = os.path.join(input_dir, f"{base_name}_v.json")

        # grib2json 명령어 실행 (U 성분: --fp 3)
        cmd_u = [
            "grib2json", "-d", "-n", "-c",
            "--fc", "1", "--fp", "3", "--fs", "160", "--fv", "0",
            "-o", u_json_path, grb_path
        ]

        # grib2json 명령어 실행 (V 성분: --fp 2)
        cmd_v = [
            "grib2json", "-d", "-n", "-c",
            "--fc", "1", "--fp", "2", "--fs", "160", "--fv", "0",
            "-o", v_json_path, grb_path
        ]

        try:
            print(f"  -> U 성분 변환 중...")
            subprocess.run(cmd_u, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            print(f"  -> V 성분 변환 중...")
            subprocess.run(cmd_v, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        except subprocess.CalledProcessError as e:
            print(f"  [오류] grib2json 실행 실패 ({base_name}): {e.stderr.decode('utf-8')}")
            continue

        # 3. 생성된 U, V JSON 파일을 읽어와서 병합 리스트에 추가
        for json_path in [u_json_path, v_json_path]:
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                        if isinstance(data, list):
                            merged_data.extend(data)
                        elif isinstance(data, dict):
                            merged_data.append(data)
                    except json.JSONDecodeError as err:
                        print(f"  [오류] JSON 파싱 실패 ({json_path}): {err}")

                # 변환 완료된 임시 개별 JSON 파일 삭제 (정리)
                os.remove(json_path)

    # 4. 최종 병합된 데이터를 지정한 출력 디렉토리에 저장
    with open(output_file_path, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)

    print(f"\n모든 변환 및 병합 작업 완료!")
    print(f"저장된 최종 파일: {output_file_path}")

if __name__ == "__main__":
    main()
