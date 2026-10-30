#!/bin/zsh

# NOMADS GFS 1.0도 기본 URL
BASE_URL="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl"

echo "=== NOMADS 서버에서 최신 날짜 및 사이클 정보 확인 중 ==="

# 1. 페이지 HTML을 가져와서 가장 최근 날짜(예: gfs.20261030)와 가장 마지막 사이클 추출
# (HTML 내에서 gfs.YYYYMMDD 형태 중 가장 맨 앞에 나오는 최신 날짜를 찾습니다)
LATEST_DIR=$(curl -s "${BASE_URL}" | grep -oE 'gfs\.[0-9]{8}' | head -n 1)

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
for i in {0..2..3}; do
  # 3자리 숫자로 포맷팅 (예: 0 -> 000, 3 -> 003, 72 -> 072)
  f_num=$(printf "%03d" $i)

  FILENAME="gfs.t${LATEST_CYCLE}z.pgrb2.1p00.f${f_num}"

  # URL 구성 (file 파라미터와 dir 내의 파일명을 동적으로 변경)
  DOWNLOAD_URL="${BASE_URL}?file=${FILENAME}&dir=${DIR}&${PARAM}"

  echo "Downloading $FILENAME..."
  curl "$DOWNLOAD_URL" -o "$FILENAME"
done


echo "=== 모든 데이터 다운로드 완료 ==="