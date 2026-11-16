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


- **주요 기상 변수 파라미터 코드 매핑**:

| 카테고리 | 파라미터  | 변수명 | 설명 | `earth` 레이어 매핑 |
|:---|:---|:---|:---|:---|
| **2 (Momentum)** | `wind` *(or `2`, `3`)* | `UGRD` & `VGRD` | U/V 풍속 벡터 성분 (m/s) | 바람 벡터 (`wind`) |
| **0 (Temperature)** | `0` | `TMP` | 기온 (Temperature, K) | 기온 (`temp`) |
| **0 (Temperature)** | `7` | `DPT` | 이슬점 온도 (Dew Point, K) | 이슬점 |
| **1 (Moisture)** | `1` | `RH` | 상대습도 (Relative Humidity, %) | 상대습도 (`relative_humidity`) |
| **1 (Moisture)** | `54` | `PWAT` | 가강수량 (Precipitable Water, $\text{kg/m}^2$) | 총 가강수량 (`total_precipitable_water`) |
| **6 (Cloud)** | `6` | `CWAT` | 구름수량 (Cloud Water, $\text{kg/m}^2$) | 총 구름수량 (`total_cloud_water`) |
| **3 (Mass)** | `1` / `192` | `PRMSL` | 해면기압 (Pressure at MSL, Pa) | 해면기압 (`mean_sea_level_pressure`) |
| **7 (Stability)** | `6` | `CAPE` | 대기불안정도 (CAPE, J/kg) | 대기 대류 유효 전위 에너지 |


##  데이터 수집 및 변환 (RTOFS to GRIB2 JSON)

NOAA NOMADS 서버 등에서 제공하는 RTOFS(Rapid Refresh Ocean Forecast System) 해양 수치 모델의 NetCDF 데이터를 표준 지리적 위도/경도(Rectilinear Grid) 체계로 리샘플링하고, 웹 시각화 환경(`deck.gl` 등)에서 고속으로 활용할 수 있는 GRIB2 형태의 JSON 페이로드로 변환합니다.

###  주요 해결 과제 및 최적화 전략
1. **곡선 좌표계(Curvilinear Grid) 처리**
    - 해양 모델은 왜곡된 2차원 곡선 좌표계를 사용하므로 단순 1차원 슬라이싱(`ds.sel()`)이 불가능합니다.
    - `scipy.interpolate.LinearNDInterpolator`를 활용하여 불규칙한 2차원 격자점(Points)을 $1^\circ \times 1^\circ$ 정규 격자에 선형 보간합니다.
2. **경도(Longitude) 범위 정규화**
    - $360^\circ$를 초과하여 누적된 경도 값(예: $1000^\circ$ 이상)을 모듈로 연산을 통해 전지구 표준 범위($0^\circ \sim 360^\circ$)로 교정합니다.
3. **Delaunay Triangulation 뼈대 재사용을 통한 성능 최적화**
    - 매 타임스텝마다 삼각화(Triangulation)를 반복하면 심각한 성능 저하가 발생하므로, 최초 1회만 Delaunay Triangulation 기하 구조 뼈대를 빌드하고 이후 타임스텝에서는 값만 교체(`interp.values = ...`)하여 고속 처리를 수행합니다.
4. **메모리 절약형 슬라이싱 (Lazy Loading)**
    - 대용량 데이터셋 전체를 메모리에 올리지 않고, `xarray`의 Dask 기반 지연 로딩(`chunks={"MT": 1}`)과 타임스텝별 표층(`Layer=0`) 슬라이싱을 적용하여 메모리 누수를 방지합니다.

###  핵심 변환 코드 요약

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
**전체분석 및 구현상세 코드는 [Rtofs_NetCDF_to_Grib2Json.ipynb](https://github.com/unchil/earth/blob/dc399151da1c8bc7f8adc8452d2a14cb4b03aaa3/public/bin/Rtofs_NetCDF_to_Grib2Json.ipynb)  노트북에서 확인하실 수 있습니다.**

**NetCDF 데이터 다운로드 및 컨버팅 스크립트 [/public/bin](https://github.com/unchil/earth/tree/dc399151da1c8bc7f8adc8452d2a14cb4b03aaa3/public/bin) 에 실행 스크립트가 존재 합니다.**

---

##  데이터 수집 및 파이프라인 자동화 스크립트 (`public/bin/`)

프로젝트 내 `public/bin/` 디렉토리에는 NOAA NOMADS 서버로부터 대기(GFS) 및 해양(RTOFS) 데이터를 수집, 변환, 병합하는 자동화 파이프라인 스크립트가 포함되어 있습니다.

###  주요 스크립트 구성

| 스크립트 파일 | 설명 및 기능                                                                                                                                                                                   |
|:---|:------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `gfs_Collector.py` | **GFS 대기 데이터 자동 수집 및 디코딩**<br>- NOMADS 서버에서 최신 GFS 사이클 및 변수(`UGRD`, `VGRD`, `TMP`, `RH`)를 자동 다운로드<br>- SQLite 데이터베이스 저장 옵션(`--useDb Y`) 및 `grib2json` 디코딩을 지원하며, 결측 시 이전 날짜 데이터 재시도 로직 포함 |
| `RtofsCollector.py` | **RTOFS 해양 데이터 자동 수집**<br>- NOAA NOMADS 서버의 최신 `rtofs.YYYYMMDD` 폴더 탐색<br>- 최신 표층 해류 NetCDF 파일(`rtofs_glo_uv_*_hcasts.nc.gz`) 자동 다운로드 및 압축 해제 처리                                           |
| `RtofsInterpToGribJson.py` | **RTOFS NetCDF 보간 및 GRIB JSON 변환**<br>- 불규칙 곡선 좌표계를 정규 격자로 보간 (`LinearNDInterpolator`)<br>- `xarray` Dask 지연 로딩 기반 메모리 최적화 및 `catalog.json` / SQLite DB 저장 지원                             |
| `rtofs_pipeline.py` | **RTOFS 데이터 수집-변환 파이프라인 CLI**<br>- 수집(`RtofsCollector.py`) $\rightarrow$ 탐색 $\rightarrow$ 보간 및 JSON 변환(`RtofsInterpToGribJson.py`) 전 과정을 일괄 실행하는 CLI 파이프라인                                |
| `MergeGribJson.py` | **GRIB2 해류 데이터 병합**<br>- 지정된 디렉토리의 `.grb2` 파일들로부터 U/V 성분 해류 데이터를 추출하고, 단일 JSON 파일로 병합 및 자동 정렬                                                                                             |

###  파이프라인 실행 예시

1. GFS 대기 데이터 수집 (최대 예보시간 72시간, DB 삽입 사용)
```python
   python3 public/bin/gfs_Collector.py --maxH 72 --retry 3 --useDb Y
```   
2. RTOFS 해양 수집-변환 일괄 파이프라인 실행
```python
   python3 public/bin/rtofs_pipeline.py --mt_range 0 8 --useDb N --output_dir ./output
```    
3. GRIB2 해류 파일 병합
```python
   python3 public/bin/MergeGribJson.py ./download ./output --output_filename merged_currents.json
```   