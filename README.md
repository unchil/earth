earth
=====

### Project Origin & Configuration Note
***"earth" can be found on GitHub at https://github.com/cambecc/earth. This is a copied project.***
**NOTE: the location of `dev-server.js` has changed from `{repository}/server/` to `{repository}/`**
"earth" is a project to visualize global weather conditions.
A customized instance of "earth" is available at http://earth.nullschool.net.

getting weather data
--------------------

### Updated NOMADS GFS Download Service URL

    YYYYMMDD=<a date, for example: 20261029>, HH=<a hour, for example: 18>
    curl "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl?dir=%2Fgfs.${YYYYMMDD}%2F${HH}%2Fatmos&file=gfs.t${${HH}}z.pgrb2.1p00.f000&var_UGRD=on&var_VGRD=on&lev_10_m_above_ground=on&lev_1000_mb=on&lev_850_mb=on&lev_700_mb=on&lev_500_mb=on&lev_250_mb=on&lev_70_mb=on&lev_10_mb=on&var_PRMSL=on&var_RH=on&var_TCDC=on&var_TMP=on" -o gfs.t${HH}z.pgrb2.1p00.f000 
    curl "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?dir=%2Fgfs.${YYYYMMDD}%2F${HH}%2Fwave%2Fgridded&file=gfswave.t${HH}z.global.0p16.f000.grib2&var_UGRD=on&var_VGRD=on&lev_surface=on&var_WVDIR=on&var_WVHGT=on&var_WVPER=on" -o gfswave.t${HH}z.global.0p16.f000

### GFS Data Specification & `grib2json` Parsing Guide

#### 1. Available Ranges and Values (Earth Height Layers)

In `index.html` and `earth.js`, the buttons under the **Height** menu (`class="surface"`) define the allowed combinations:

| `surface` | `level` | Altitude / Layer Meaning | Meteorological Significance |
|:---|:---|:---|:---|
| `"surface"` | `"level"` | Ground / Sea Surface Level | Surface winds (10 m), ground temp/humidity |
| `"isobaric"` | `"1000hPa"` | ~100 m | Near sea level atmospheric conditions |
| `"isobaric"` | `"850hPa"` | ~1,500 m | Planetary boundary layer (low) |
| `"isobaric"` | `"700hPa"` | ~3,500 m | Planetary boundary layer (high) |
| `"isobaric"` | `"500hPa"` | ~5,000 m | Mid-troposphere (vorticity & steering flows) |
| `"isobaric"` | `"250hPa"` | ~10,500 m | Upper troposphere (jet stream level) |
| `"isobaric"` | `"70hPa"` | ~17,500 m | Lower stratosphere |
| `"isobaric"` | `"10hPa"` | ~26,500 m | Stratosphere |
| `undefined` | `undefined` | Entire atmospheric column | Integrated scalar overlays (`TPW`, `TCW`, `MSLP`) |

---

#### 2. `--filter.parameter` (`--fp`) 정의 및 주요 기상 변수 매핑

- **WMO GRIB2 계층 구조**:
    - `Discipline` (`--fd`): 대분류 (예: `0` = 기상학적 프로덕트)
    - `Category` (`--fc`): 기상 카테고리 (예: `0` = 온도, `1` = 수분, `2` = 운동량, `3` = 질량, `6` = 구름)
    - `Parameter` (`--fp`): 카테고리 내 세부 물리량 번호 (또는 특수 키워드 `"wind"`)

- **특수 키워드 `"wind"`**:
    - `--fp wind`: 바람 벡터 연산에 필요한 `UGRD`(동서 성분, Category 2 / Parameter 2)와 `VGRD`(남북 성분, Category 2 / Parameter 3)를 한 번에 동시 추출합니다.

- **주요 기상 변수 파라미터 코드 매핑**:

| 카테고리 (`--fc`) | 파라미터 (`--fp`) | 변수명 | 설명 | `earth` 레이어 매핑 |
|:---|:---|:---|:---|:---|
| **2 (Momentum)** | `wind` *(or `2`, `3`)* | `UGRD` & `VGRD` | U/V 풍속 벡터 성분 (m/s) | 바람 벡터 (`wind`) |
| **0 (Temperature)** | `0` | `TMP` | 기온 (Temperature, K) | 기온 (`temp`) |
| **0 (Temperature)** | `7` | `DPT` | 이슬점 온도 (Dew Point, K) | 이슬점 |
| **1 (Moisture)** | `1` | `RH` | 상대습도 (Relative Humidity, %) | 상대습도 (`relative_humidity`) |
| **1 (Moisture)** | `54` | `PWAT` | 가강수량 (Precipitable Water, $\text{kg/m}^2$) | 총 가강수량 (`total_precipitable_water`) |
| **6 (Cloud)** | `6` | `CWAT` | 구름수량 (Cloud Water, $\text{kg/m}^2$) | 총 구름수량 (`total_cloud_water`) |
| **3 (Mass)** | `1` / `192` | `PRMSL` | 해면기압 (Pressure at MSL, Pa) | 해면기압 (`mean_sea_level_pressure`) |
| **7 (Stability)** | `6` | `CAPE` | 대기불안정도 (CAPE, J/kg) | 대기 대류 유효 전위 에너지 |




#### 3. GFS 데이터 계층(Surface / Level)별 `grib2json` 파싱 옵션 매핑 표

| `surface` | `level` | 고도 / 기압 | 기상학적 의미 | `grib2json` 필터 옵션 (`--fs`, `--fv`, `--fp`) | 생성 파일명 (Wind 기준 예시) |
|:---|:---|:---|:---|:---|:---|
| `"surface"` | `"level"` | 지표면 / 10m | 지표 바람 (10 m), 지상 기온/습도 | `--fp wind --fs 103 --fv 10` *(지표 10m 바람)*<br>`--fp 0 --fs 103 --fv 2` *(지상 2m 기온)*<br>`--fs 1 --fv 0` *(지표면/해수면)* | `0000-wind-surface-level-gfs-1.0.json` |
| `"isobaric"` | `"1000hPa"` | ~100 m | 해수면 인근 대기 상태 (약 1,000 hPa) | `--fp wind --fs 100 --fv 100000` | `0000-wind-isobaric-1000hPa-gfs-1.0.json` |
| `"isobaric"` | `"850hPa"` | ~1,500 m | 대기 경계층 상단 / 하층운 (850 hPa) | `--fp wind --fs 100 --fv 85000` | `0000-wind-isobaric-850hPa-gfs-1.0.json` |
| `"isobaric"` | `"700hPa"` | ~3,500 m | 하층 대기 / 습윤 구름대 (700 hPa) | `--fp wind --fs 100 --fv 70000` | `0000-wind-isobaric-700hPa-gfs-1.0.json` |
| `"isobaric"` | `"500hPa"` | ~5,000 m | 중층 대기 (와도 분석 및 기압골/기압능) | `--fp wind --fs 100 --fv 50000` | `0000-wind-isobaric-500hPa-gfs-1.0.json` |
| `"isobaric"` | `"250hPa"` | ~10,500 m | 상층 대류권 (제트기류 / Jet Stream) | `--fp wind --fs 100 --fv 25000` | `0000-wind-isobaric-250hPa-gfs-1.0.json` |
| `"isobaric"` | `"70hPa"` | ~17,500 m | 하층 성층권 | `--fp wind --fs 100 --fv 7000` | `0000-wind-isobaric-70hPa-gfs-1.0.json` |
| `"isobaric"` | `"10hPa"` | ~26,500 m | 성층권 상부 (극야 제트 등) | `--fp wind --fs 100 --fv 1000` | `0000-wind-isobaric-10hPa-gfs-1.0.json` |
| `undefined` | `undefined` | 전체 대기 컬럼 / 해면 | 통합 오버레이 (`TPW`, `TCW`, `MSLP` 등) | `--fs 200 --fv 0` *(전체 대기층 - PWAT)*<br>`--fs 101 --fv 0` *(해면기압 - PRMSL)*<br>`--fs 1 --fv 0` *(지표 - CAPE)* | `current-total_precipitable_water-gfs-1.0.json`<br>`current-mean_sea_level_pressure-gfs-1.0.json` |

---

#### 4. `grib2json` 주요 옵션 상세 설명

- **공통 필수 플래그**:
  - `-d` (`--data`): 그리드 배열 데이터(`"data": [...]`)를 출력에 포함합니다 (시각화 연산 필수).
  - `-n` (`--names`): 메타데이터에 사람이 읽을 수 있는 명칭(문자열)을 추가합니다.
  - `-c` (`--compact`): 배열 포맷의 공백/줄바꿈을 압축하여 JSON 용량을 대폭 줄입니다.

- **표면 유형 (`--fs`, `--filter.surface`) 코드**:
  - `1`: Ground or water surface (지표면/해수면)
  - `100`: Isobaric surface (등압면)
  - `101`: Mean sea level (평균 해수면)
  - `103`: Specified height level above ground (지상 특정 높이)
  - `200`: Entire atmosphere considered as a single layer (단일 층으로 간주된 전체 대기)

- **표면 값 (`--fv`, `--filter.value`) 단위**:
  - **등압면 (`--fs 100`)**: 단위가 파스칼(Pa)입니다. (1 hPa = 100 Pa)
    - 1000 hPa $\rightarrow$ 100000
    - 850 hPa $\rightarrow$ 85000
    - 700 hPa $\rightarrow$ 70000
    - 500 hPa $\rightarrow$ 50000
    - 250 hPa $\rightarrow$ 25000
    - 70 hPa $\rightarrow$ 7000
    - 10 hPa $\rightarrow$ 1000
  - **지상 고도 (`--fs 103`)**: 단위가 미터(m)입니다.
    - 지표 바람(10m): `--fv 10`
    - 지상 기온/습도(2m): `--fv 2`

---

#### 5. 대표 변환 명령어 예시

```zsh
# 1. 지표 바람 (Surface Wind, 10m above ground)
grib2json -d -n -c --fp wind --fs 100 --fv 100000 -o current-wind-isobaric-1000hPa-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000
# 2. 상대습도 (Relative Humidity, %)
grib2json -d -n -c --fc 1 --fp 1 --fs 100 --fv 100000 -o current-relative_humidity-isobaric-1000hPa-gfs-1.0.json  gfs.t00z.pgrb2.1p00.f000
# 3. 등압면 기온 (Temperature)
grib2json -d -n -c --fc 0 --fp 0 --fs 100 --fv 100000 -o current-temp-isobaric-1000hPa-gfs-1.0.json  gfs.t00z.pgrb2.1p00.f000
```

---

## 🌊 데이터 수집 및 변환 (RTOFS to GRIB2 JSON)

NOAA NOMADS 서버 등에서 제공하는 RTOFS(Rapid Refresh Ocean Forecast System) 해양 수치 모델의 NetCDF 데이터를 표준 지리적 위도/경도(Rectilinear Grid) 체계로 리샘플링하고, 웹 시각화 환경(`deck.gl` 등)에서 고속으로 활용할 수 있는 GRIB2 형태의 JSON 페이로드로 변환합니다.

### 🛠️ 주요 해결 과제 및 최적화 전략
1. **곡선 좌표계(Curvilinear Grid) 처리**
    - 해양 모델은 왜곡된 2차원 곡선 좌표계를 사용하므로 단순 1차원 슬라이싱(`ds.sel()`)이 불가능합니다.
    - `scipy.interpolate.LinearNDInterpolator`를 활용하여 불규칙한 2차원 격자점(Points)을 $1^\circ \times 1^\circ$ 정규 격자에 선형 보간합니다.
2. **경도(Longitude) 범위 정규화**
    - $360^\circ$를 초과하여 누적된 경도 값(예: $1000^\circ$ 이상)을 모듈로 연산을 통해 전지구 표준 범위($0^\circ \sim 360^\circ$)로 교정합니다.
3. **Delaunay Triangulation 뼈대 재사용을 통한 성능 최적화**
    - 매 타임스텝마다 삼각화(Triangulation)를 반복하면 심각한 성능 저하가 발생하므로, 최초 1회만 Delaunay Triangulation 기하 구조 뼈대를 빌드하고 이후 타임스텝에서는 값만 교체(`interp.values = ...`)하여 고속 처리를 수행합니다.
4. **메모리 절약형 슬라이싱 (Lazy Loading)**
    - 대용량 데이터셋 전체를 메모리에 올리지 않고, `xarray`의 Dask 기반 지연 로딩(`chunks={"MT": 1}`)과 타임스텝별 표층(`Layer=0`) 슬라이싱을 적용하여 메모리 누수를 방지합니다.

### 💻 핵심 변환 코드 요약

```python
import os
import xarray as xr
import pandas as pd
import numpy as np
import time
from scipy.interpolate import LinearNDInterpolator
import json
import gc

# 1. 데이터셋 로드 (Lazy Loading)
file_path = "./download/rtofs_glo_uv_20261106_hcasts.nc"
ds = xr.open_dataset(file_path, chunks={"MT": 1})

# 2. 경도 정규화 (0 ~ 360 범위)
ds['Longitude'] = xr.where(ds['Longitude'].compute() > 360, ds['Longitude'].compute() % 360, ds['Longitude'].compute())

# 3. 1도 정규 격자 정의
target_lons = np.arange(0, 360, 1.0)
target_lats = np.arange(90, -91, -1.0)
grid_lon, grid_lat = np.meshgrid(target_lons, target_lats)
ny, nx = grid_lon.shape

# 4. 2차원 좌표 평탄화 및 결측치 마스킹
lon_2d = ds['Longitude'].compute().values.flatten()
lat_2d = ds['Latitude'].compute().values.flatten()

u_sample = ds['u_velocity'].isel(MT=0, Layer=0).compute().values.flatten()
valid_mask = ~np.isnan(u_sample) & ~np.isnan(lat_2d) & ~np.isnan(lon_2d)
points = np.column_stack((lon_2d[valid_mask], lat_2d[valid_mask]))

# 5. 최초 1회 LinearNDInterpolator 뼈대 구축 (핵심 성능 최적화)
u_vals_sample = u_sample[valid_mask].astype(np.float64)
interp_u = LinearNDInterpolator(points, u_vals_sample)
```
**전체분석 및 구현상세 코드는 [Rtofs_NetCDF_to_Grib2Json.ipynb](https://github.com/unchil/ComposeDataGrid/blob/main/Rtofs_NetCDF_to_Grib2Json.ipynb)  노트북에서 확인하실 수 있습니다.**