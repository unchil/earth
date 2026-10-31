#!/bin/zsh

# NOMADS GFS 1.0도 기본 URL
BASE_URL="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
DIR_DOWNLOAD="./download"

echo "=== NOMADS 서버에서 최신 날짜 및 사이클 정보 확인 중 ==="

# 1. 페이지 HTML을 가져와서 가장 최근 날짜(예: gfs.20261030)와 가장 마지막 사이클 추출
# (HTML 내에서 gfs.YYYYMMDD 형태 중 가장 맨 앞에 나오는 최신 날짜를 찾습니다)
LATEST_DIR=$(curl -s "${BASE_URL}" | grep -oE '">gfs\.[0-9]{8}</a>' | grep -oE 'gfs\.[0-9]{8}' | head -n 1 )

if [ -z "$LATEST_DIR" ]; then
    echo "오류: 최신 날짜 디렉터리를 가져오지 못했습니다. 수동 설정으로 전환합니다."
    DATE=$(date -u +%Y%m%d)
    LATEST_DIR="gfs.${DATE}"
else
    DATE=$(echo "$LATEST_DIR" | cut -d'.' -f2)
fi

# 2. 해당 날짜 폴더 내의 사이클(Cycle: 00, 06, 12, 18 등) 중 가장 숫자가 큰(최신) 사이클 추출
LATEST_CYCLE=$( curl -s "${BASE_URL}?dir=/${LATEST_DIR}" | grep -oE '>[0-9]{2}</a>' | grep -oE '[0-9]{2}' | sort -n | tail -n 1 )

if [ -z "$LATEST_CYCLE" ]; then
   echo "오류: 최신 사이클을 가져오지 못했습니다. 수동 설정으로 전환합니다."
    LATEST_CYCLE="18"
fi

echo "-> 감지된 최신 날짜: ${DATE} (${LATEST_DIR})"
echo "-> 감지된 가장 마지막 사이클: ${LATEST_CYCLE}"
echo "=== 다운로드 시작 ==="

# 기본 URL 및 디렉터리 구조 설정

DIR="/${LATEST_DIR}/${LATEST_CYCLE}/atmos"
PARAM="var_UGRD=on&var_VGRD=on&lev_10_m_above_ground=on&lev_1000_mb=on&lev_850_mb=on&lev_700_mb=on&lev_500_mb=on&lev_250_mb=on&lev_70_mb=on&lev_10_mb=on&var_PRMSL=on&var_RH=on&var_TCDC=on&var_TMP=on"

# 0시간부터 72시간까지 3시간 간격 (000, 003, ..., 072)
for i in {0..72..3}; do
  # 3자리 숫자로 포맷팅 (예: 0 -> 000, 3 -> 003, 72 -> 072)
  f_num=$(printf "%03d" $i)

  FILENAME="gfs.t${LATEST_CYCLE}z.pgrb2.1p00.f${f_num}"

  # URL 구성 (file 파라미터와 dir 내의 파일명을 동적으로 변경)
  DOWNLOAD_URL="${BASE_URL}?file=${FILENAME}&dir=${DIR}&${PARAM}"

  echo "Downloading $FILENAME..."
  curl "$DOWNLOAD_URL" -o "${DIR_DOWNLOAD}/${FILENAME}"
done


echo "=== 모든 데이터 다운로드 완료 ==="


DIR_ATMOS="./weather"

# Available Ranges and Values: (surface, level, filter_surface, filter_value)
LAYERS=(
    "surface:level:103:10"
    "isobaric:1000hPa:100:100000"
    "isobaric:850hPa:100:85000"
    "isobaric:700hPa:100:70000"
    "isobaric:500hPa:100:50000"
    "isobaric:250hPa:100:25000"
    "isobaric:70hPa:100:7000"
    "isobaric:10hPa:100:1000"
)

echo "=== grib2json 디코딩 시작 ==="

for src_file in "${DIR_DOWNLOAD}"/gfs.t*z.pgrb2.1p00.f*; do
    [ -f "${src_file}" ] || continue
    filename=$(basename "${src_file}")

    # 파일명 끝 3자리 숫자 추출 (예: f000 -> 000, f003 -> 003, ..., f072 -> 072)
    f_num="${filename: -3}"

    # 3자리 숫자를 정수로 변환하여 LATEST_CYCLE에 더해질 오프셋 시간(시간 단위) 계산
    offset_hours=$(( 10#${f_num} ))

    # DATE와 LATEST_CYCLE을 기준으로 offset_hours를 더한 대상 날짜 디렉터리(YYYY/MM/DD) 계산
    # macOS(BSD date)는 -j, -v, -f 옵션을 사용하지만, 리눅스(GNU date)는 -d 옵션을 사용
    # 출력 포멧 지정시 반드시 + 와 %Y/%m/%d 의 사이에 공백이 있으면 에러. +"%Y/%m/%d"
    if date -v+1d >/dev/null 2>&1; then
      # macOS / BSD date
        target_date_dir=$(date -u -j -v+"${offset_hours}"H -f "%Y%m%d%H" "${DATE}${LATEST_CYCLE}" +"%Y/%m/%d")
    else
       # Linux GNU date
        target_date_dir=$(date -u -d "${DATE} ${LATEST_CYCLE}:00 + ${offset_hours} hours" +"%Y/%m/%d")
    fi

    target_dir="${DIR_ATMOS}/${target_date_dir}"
    mkdir -p "${target_dir}"


    # 저장파일명의 첫 번째 '-' 앞 접두사: LATEST_CYCLE 기준 offset_hours를 더한 UTC 시각(HHMM)
    prefix=$(date -u -j -v+"${offset_hours}"H -f "%H%M" "${LATEST_CYCLE}00" +"%H%M")

    for layer in "${LAYERS[@]}"; do
        IFS=':' read -r SURFACE LEVEL FS FV <<< "$layer"
        target_filename="${prefix}-wind-${SURFACE}-${LEVEL}-gfs-1.0.json"
        target_file_path="${target_dir}/${target_filename}"

        echo "Decoding ${filename} -> ${target_date_dir}/${target_filename} (surface: ${FS}, value: ${FV})..."
        grib2json -d -n -c --fp wind --fs "${FS}" --fv "${FV}" -o "${target_file_path}" "${src_file}"

        # prefix가 "0000"일 경우 DIR_ATMOS/current 에도 current-wind- 파일로 복사/추가
        if [ "${prefix}" = "${LATEST_CYCLE}00" ]; then
            current_dir="${DIR_ATMOS}/current"
            mkdir -p "${current_dir}"
            cp "${target_file_path}" "${current_dir}/current-wind-${SURFACE}-${LEVEL}-gfs-1.0.json"
        fi
    done
done

echo "=== 모든 데이터 디코딩 완료 ==="


