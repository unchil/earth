earth
=====

### un7 comment: Project Origin & Configuration Note
***"earth" can be found on GitHub at https://github.com/cambecc/earth. This is a copied project.***

**NOTE: the location of `dev-server.js` has changed from `{repository}/server/` to `{repository}/`**

"earth" is a project to visualize global weather conditions.

A customized instance of "earth" is available at http://earth.nullschool.net.

"earth" is a personal project I've used to learn javascript and browser programming, and is based on the earlier
[Tokyo Wind Map](https://github.com/cambecc/air) project.  Feedback and contributions are welcome! ...especially
those that clarify accepted best practices.

building and launching
----------------------

After installing node.js and npm, clone "earth" and install dependencies:

    git clone https://github.com/cambecc/earth
    cd earth
    npm install

Next, launch the development web server:

    node dev-server.js 8080

Finally, point your browser to:

    http://localhost:8080

The server acts as a stand-in for static S3 bucket hosting and so contains almost no server-side logic. It
serves all files located in the `earth/public` directory. See `public/index.html` and `public/libs/earth/*.js`
for the main entry points. Data files are located in the `public/data` directory, and there is one sample
weather layer located at `data/weather/current`.

*For Ubuntu, Mint, and elementary OS, use `nodejs` instead of `node` instead due to a [naming conflict](https://github.com/joyent/node/wiki/Installing-Node.js-via-package-manager#ubuntu-mint-elementary-os).

getting map data
----------------

Map data is provided by [Natural Earth](http://www.naturalearthdata.com) but must be converted to
[TopoJSON](https://github.com/mbostock/topojson/wiki) format. We make use of a couple different map scales: a
simplified, larger scale for animation and a more detailed, smaller scale for static display. After installing
[GDAL](http://www.gdal.org/) and TopoJSON (see [here](http://bost.ocks.org/mike/map/#installing-tools)), the
following commands build these files:

    curl "http://www.nacis.org/naturalearth/50m/physical/ne_50m_coastline.zip" -o ne_50m_coastline.zip
    curl "http://www.nacis.org/naturalearth/50m/physical/ne_50m_lakes.zip" -o ne_50m_lakes.zip
    curl "http://www.nacis.org/naturalearth/110m/physical/ne_110m_coastline.zip" -o ne_110m_coastline.zip
    curl "http://www.nacis.org/naturalearth/110m/physical/ne_110m_lakes.zip" -o ne_110m_lakes.zip
    unzip -o ne_\*.zip
    ogr2ogr -f GeoJSON coastline_50m.json ne_50m_coastline.shp
    ogr2ogr -f GeoJSON coastline_110m.json ne_110m_coastline.shp
    ogr2ogr -f GeoJSON -where "scalerank < 4" lakes_50m.json ne_50m_lakes.shp
    ogr2ogr -f GeoJSON -where "scalerank < 2 AND admin='admin-0'" lakes_110m.json ne_110m_lakes.shp
    ogr2ogr -f GeoJSON -simplify 1 coastline_tiny.json ne_110m_coastline.shp
    ogr2ogr -f GeoJSON -simplify 1 -where "scalerank < 2 AND admin='admin-0'" lakes_tiny.json ne_110m_lakes.shp
    topojson -o earth-topo.json coastline_50m.json coastline_110m.json lakes_50m.json lakes_110m.json
    topojson -o earth-topo-mobile.json coastline_110m.json coastline_tiny.json lakes_110m.json lakes_tiny.json
    cp earth-topo*.json <earth-git-repository>/public/data/

getting weather data
--------------------

Weather data is produced by the [Global Forecast System](http://en.wikipedia.org/wiki/Global_Forecast_System) (GFS),
operated by the US National Weather Service. Forecasts are produced four times daily and made available for
download from [NOMADS](http://nomads.ncep.noaa.gov/). The files are in [GRIB2](http://en.wikipedia.org/wiki/GRIB)
format and contain over [300 records](http://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrbf00.grib2.shtml).
We need only a few of these records to visualize wind data at a particular isobar. The following commands download
the 1000 hPa wind vectors and convert them to JSON format using the [grib2json](https://github.com/cambecc/grib2json)
utility:

    
    YYYYMMDD=<a date, for example: 20140101>
    curl "http://nomads.ncep.noaa.gov/cgi-bin/filter_gfs.pl?file=gfs.t00z.pgrb2.1p00.f000&lev_10_m_above_ground=on&var_UGRD=on&var_VGRD=on&dir=%2Fgfs.${YYYYMMDD}00" -o gfs.t00z.pgrb2.1p00.f000  
    grib2json -d -n -o current-wind-surface-level-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000
    cp current-wind-surface-level-gfs-1.0.json <earth-git-repository>/public/data/weather/current


### un7 comment: Updated NOMADS GFS Download Service URL

    YYYYMMDD=<a date, for example: 20261029>, HH=<a hour, for example: 18>
    curl "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl?dir=%2Fgfs.${YYYYMMDD}%2F${HH}%2Fatmos&file=gfs.t${${HH}}z.pgrb2.1p00.f000&var_UGRD=on&var_VGRD=on&lev_10_m_above_ground=on&lev_1000_mb=on&lev_850_mb=on&lev_700_mb=on&lev_500_mb=on&lev_250_mb=on&lev_70_mb=on&lev_10_mb=on&var_PRMSL=on&var_RH=on&var_TCDC=on&var_TMP=on" -o gfs.t${HH}z.pgrb2.1p00.f000 
    curl "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfswave.pl?dir=%2Fgfs.${YYYYMMDD}%2F${HH}%2Fwave%2Fgridded&file=gfswave.t${HH}z.global.0p16.f000.grib2&var_UGRD=on&var_VGRD=on&lev_surface=on&var_WVDIR=on&var_WVHGT=on&var_WVPER=on" -o gfswave.t${HH}z.global.0p16.f000

### un7 comment: GFS Data Specification & `grib2json` Parsing Guide

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
grib2json -d -n -c --fp wind --fs 103 --fv 10 -o 0000-wind-surface-level-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000

# 2. 250 hPa 등압면 바람 (Jet Stream)
grib2json -d -n -c --fp wind --fs 100 --fv 25000 -o 0000-wind-isobaric-250hPa-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000

# 3. 850 hPa 등압면 기온 (Temperature)
grib2json -d -n -c --fc 0 --fp 0 --fs 100 --fv 85000 -o 0000-temp-isobaric-850hPa-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000

# 4. 상대습도 (Relative Humidity, %)
grib2json -d -n -c --fc 1 --fp 1  -o 0000-rh-isobaric-1000hPa-gfs-1.0.json gfs.t00z.pgrb2.1p00.f000

# 5. 지구 전체의 파고(Significant Wave Height), 파향, 주기의 분포
grib2json -d -o output.json gfswave.t18z.global.0p16.f000
```

---



font subsetting
---------------

This project uses [M+ FONTS](http://mplus-fonts.sourceforge.jp/). To reduce download size, a subset font is
constructed out of the unique characters utilized by the site. See the `earth/server/font/findChars.js` script
for details. Font subsetting is performed by the [M+Web FONTS Subsetter](http://mplus.font-face.jp/), and
the resulting font is placed in `earth/public/styles`.

[Mono Social Icons Font](http://drinchev.github.io/monosocialiconsfont/) is used for scalable, social networking
icons. This can be subsetted using [Font Squirrel's WebFont Generator](http://www.fontsquirrel.com/tools/webfont-generator).

implementation notes
--------------------

Building this project required solutions to some interesting problems. Here are a few:

   * The GFS grid has a resolution of 1°. Intermediate points are interpolated in the browser using [bilinear
     interpolation](http://en.wikipedia.org/wiki/Bilinear_interpolation). This operation is quite costly.
   * Each type of projection warps and distorts the earth in a particular way, and the degree of distortion must
     be calculated for each point (x, y) to ensure wind particle paths are rendered correctly. For example,
     imagine looking at a globe where a wind particle is moving north from the equator. If the particle starts
     from the center, it will trace a path straight up. However, if the particle starts from the globe's edge,
     it will trace a path that curves toward the pole. [Finite difference approximations](http://gis.stackexchange.com/a/5075/23451)
     are used to estimate this distortion during the interpolation process.
   * The SVG map of the earth is overlaid with an HTML5 Canvas, where the animation is drawn. Another HTML5
     Canvas sits on top and displays the colored overlay. Both canvases must know where the boundaries of the
     globe are rendered by the SVG engine, but this pixel-for-pixel information is difficult to obtain directly
     from the SVG elements. To workaround this problem, the globe's bounding sphere is re-rendered to a
     detached Canvas element, and the Canvas' pixels operate as a mask to distinguish points that lie outside
     and inside the globe's bounds.
   * Most configuration options are persisted in the hash fragment to allow deep linking and back-button
     navigation. I use a [backbone.js Model](http://backbonejs.org/#Model) to represent the configuration.
     Changes to the model persist to the hash fragment (and vice versa) and trigger "change" events which flow to
     other components.
   * Components use [backbone.js Events](http://backbonejs.org/#Events) to trigger changes in other downstream
     components. For example, downloading a new layer produces a new grid, which triggers reinterpolation, which
     in turn triggers a new particle animator. Events flow through the page without much coordination,
     sometimes causing visual artifacts that (usually) quickly disappear.
   * There's gotta be a better way to do this. Any ideas?

inspiration
-----------

The awesome [hint.fm wind map](http://hint.fm/wind/) and [D3.js visualization library](http://d3js.org) provided
the main inspiration for this project.
