#!/bin/zsh


# 사용법: ./atmosCollector.sh [최대예보시간(기본 72)] [간격(기본 3)]
# 예) ./atmosCollector.sh          -> 0~72시간, 3시간 간격
#     ./atmosCollector.sh 120     -> 0~120시간, 3시간 간격
MAX_HOUR="${1:-72}"

if [[ ! "$MAX_HOUR" =~ ^[0-9]+$ ]]; then
    echo "사용법: $0 [최대예보시간(정수, 기본 72)]"
    exit 1
fi

echo "=== 입력된 최대예보시간:${MAX_HOUR} ==="


# NOMADS GFS 1.0도 기본 URL
BASE_URL="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"
DIR_DOWNLOAD="./download"

# 다운로드할 변수 목록 (추가/삭제 시 이 배열만 수정) -> var_<이름>=on
VARIABLES=(
    "UGRD"
    "VGRD"
    "PRMSL"
    "RH"
    "TCDC"
    "TMP"
)

# 다운로드할 레벨 목록 (추가/삭제 시 이 배열만 수정) -> lev_<이름>=on
LEVELS=(
    "10_m_above_ground"
    "1000_mb"
    "850_mb"
    "700_mb"
    "500_mb"
    "250_mb"
    "70_mb"
    "10_mb"
)

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

# VARIABLES, LEVELS 배열로부터 쿼리 파라미터 생성
PARAM=""
for v in "${VARIABLES[@]}"; do
    PARAM+="&var_${v}=on"
done
for l in "${LEVELS[@]}"; do
    PARAM+="&lev_${l}=on"
done
PARAM="${PARAM#&}"

# 다운로드 전 DIR_DOWNLOAD 비우기 (디렉터리가 없으면 생성)
# ${DIR_DOWNLOAD:?} : 변수가 비어 있으면 중단하여 "/*" 삭제 사고 방지
# (N) : zsh glob 한정자, 비어 있는 디렉터리일 때 "no matches found" 에러 방지
mkdir -p "${DIR_DOWNLOAD}"
rm -rf "${DIR_DOWNLOAD:?}"/*(N)
echo "-> ${DIR_DOWNLOAD} 디렉터리를 비웠습니다."

# 0시간부터 72시간까지 3시간 간격 (000, 003, ..., 072)
for (( i = 0; i <= MAX_HOUR; i += 3 )); do
  # 3자리 숫자로 포맷팅 (예: 0 -> 000, 3 -> 003, 72 -> 072)
  f_num=$(printf "%03d" $i)

  FILENAME="gfs.t${LATEST_CYCLE}z.pgrb2.1p00.f${f_num}"

  # URL 구성 (file 파라미터와 dir 내의 파일명을 동적으로 변경)
  DOWNLOAD_URL="${BASE_URL}?file=${FILENAME}&dir=${DIR}&${PARAM}"

  echo "Downloading $FILENAME..."
  TMP_FILE="${DIR_DOWNLOAD}/${FILENAME}.tmp"
  curl -s "$DOWNLOAD_URL" -o "${TMP_FILE}"

  # 서버가 GRIB2 대신 "data file is not present" HTML 을 돌려준 경우 저장하지 않고 스킵
  if grep -qa ">data file is not present" "${TMP_FILE}"; then
    # 최초 발견 시: 하루 이전 날짜의 마지막 사이클로 전환 후 f000 부터 다시 다운로드
    if (( i == 3 )); then
      echo "-> ${FILENAME}: 서버에 데이터 파일이 없습니다. 하루 이전 날짜의 마지막 사이클로 전환합니다."

      # 하루 이전 날짜 계산 (macOS / BSD date 와 Linux GNU date 분기)
      if date -v+1d >/dev/null 2>&1; then
        DATE=$(date -u -j -v-1d -f "%Y%m%d" "${DATE}" +"%Y%m%d")
      else
        DATE=$(date -u -d "${DATE} -1 day" +"%Y%m%d")
      fi
      LATEST_DIR="gfs.${DATE}"

      # 이전 날짜 폴더 내의 마지막 사이클 추출 (실패 시 18)
      LATEST_CYCLE=$( curl -s "${BASE_URL}?dir=/${LATEST_DIR}" | grep -oE '>[0-9]{2}</a>' | grep -oE '[0-9]{2}' | sort -n | tail -n 1 )
      LATEST_CYCLE="${LATEST_CYCLE:-18}"
      DIR="/${LATEST_DIR}/${LATEST_CYCLE}/atmos"

      echo "-> 전환된 날짜: ${DATE} (${LATEST_DIR}), 사이클: ${LATEST_CYCLE}"

      # 이전 사이클로 받은 파일이 섞이지 않도록 DIR_DOWNLOAD 를 다시 비움
      rm -rf "${DIR_DOWNLOAD:?}"/*(N)

      # 다음 반복에서 i += 3 이 적용되어 i = 0 (f000) 부터 다시 시작
      i=0
      continue
    fi

    echo "-> ${FILENAME}: 서버에 데이터 파일이 아직 없습니다. 스킵합니다."
    rm -f "${TMP_FILE}"
    continue
  fi

  mv "${TMP_FILE}" "${DIR_DOWNLOAD}/${FILENAME}"
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

# (N) : 매칭되는 파일이 없으면 에러 대신 빈 배열이 됨
SRC_FILES=( "${DIR_DOWNLOAD}"/gfs.t*z.pgrb2.1p00.f*(N) )
if (( ${#SRC_FILES} == 0 )); then
    echo "정보: ${DIR_DOWNLOAD} 에 디코딩할 GRIB2 파일이 없습니다. 종료합니다."
    exit 1
fi

for src_file in "${SRC_FILES[@]}"; do
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

        # f_num "000"일 경우 DIR_ATMOS/current 에도 current-wind- 파일로 복사/추가
        if [ "${f_num}" = "000" ]; then
            current_dir="${DIR_ATMOS}/current"
            mkdir -p "${current_dir}"
            cp "${target_file_path}" "${current_dir}/current-wind-${SURFACE}-${LEVEL}-gfs-1.0.json"
        fi
    done
done

echo "=== 모든 데이터 디코딩 완료 ==="


