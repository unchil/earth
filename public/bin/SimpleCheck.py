import json
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.ops import polygonize
from shapely.validation import make_valid
import topojson as tp

DATA_DIR = "/Library/WebServer/Documents/data"
TOPO_PATH = f"{DATA_DIR}/earth-topo.json"


def normalize_lon_180(lon: float) -> float:
    """경도를 [-180, 180] 범위로 정규화합니다."""
    lon_360 = lon % 360.0
    return ((lon_360 + 180.0) % 360.0) - 180.0


def _clean_gdf_4326(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """지오메트리를 유효화하고 EPSG:4326을 세팅합니다."""
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry").set_crs("EPSG:4326", allow_override=True)

    out = gdf[["geometry"]].copy()
    out["geometry"] = out.geometry.apply(make_valid)
    out = out[out.geometry.notna() & (~out.geometry.is_empty) & out.geometry.is_valid].copy()
    return gpd.GeoDataFrame(out, geometry="geometry").set_crs("EPSG:4326", allow_override=True)


def load_land_polygons(
        topo_path: str = TOPO_PATH,
        polygon_layers: tuple[str, ...] = ("countries1", "land1", "countries2", "land2", "land3", "land4"),
        fallback_coast_layer: str = "coastline_50m",
        buffer_meters: float = 0.0,
) -> gpd.GeoDataFrame:
    """land / countries 폴리곤 레이어를 우선 사용하고, 없으면 해안선 polygonize로 대체합니다."""
    with open(topo_path, "r", encoding="utf-8") as f:
        topo_data = json.load(f)

    available_objects = list(topo_data.get("objects", {}).keys())
    gdfs: list[gpd.GeoDataFrame] = []

    for obj_name in polygon_layers:
        if obj_name in available_objects:
            gdf = tp.Topology(topo_data, object_name=obj_name).to_gdf()
            cleaned = _clean_gdf_4326(gdf)
            if not cleaned.empty:
                gdfs.append(cleaned)

    # 폴리곤 레이어가 없을 때만 coastline_50m으로 polygonize 수행
    if not gdfs and fallback_coast_layer in available_objects:
        gdf_coast = tp.Topology(topo_data, object_name=fallback_coast_layer).to_gdf()
        polys = [make_valid(p) for p in polygonize(gdf_coast.geometry.union_all())]
        gdf_poly = _clean_gdf_4326(gpd.GeoDataFrame(geometry=polys))
        if not gdf_poly.empty:
            gdfs.append(gdf_poly)

    if not gdfs:
        raise ValueError(f"사용 가능한 육지 레이어가 없습니다. 존재 객체: {available_objects}")

    merged = pd.concat(gdfs, ignore_index=True)
    land_gdf = _clean_gdf_4326(merged)

    if buffer_meters != 0.0 and not land_gdf.empty:
        land_proj = land_gdf.to_crs("ESRI:54035")
        land_proj["geometry"] = land_proj.geometry.buffer(buffer_meters)
        land_gdf = _clean_gdf_4326(land_proj.to_crs("EPSG:4326"))

    return land_gdf[["geometry"]].reset_index(drop=True)


def is_on_land(lat: float, lon: float, land_gdf: gpd.GeoDataFrame) -> bool:
    """주어진 (lat, lon)이 육지 폴리곤 내부에 포함되면 True, 아니면 False를 반환합니다."""
    pt = Point(normalize_lon_180(lon), lat)
    return bool(land_gdf.geometry.contains(pt).any())


def validate_land_gdf(land_gdf: gpd.GeoDataFrame) -> None:
    """육지 폴리곤 GeoDataFrame과 기준 좌표 판정 결과를 검증합니다."""
    print("=== 1. 폴리곤 레이어 검증 ===")
    print(f"CRS: {land_gdf.crs}")
    print(f"폴리곤 개수: {len(land_gdf)}")
    print(f"전체 경위도 bounds: {land_gdf.total_bounds.tolist()}")
    print(f"empty 개수: {int(land_gdf.geometry.is_empty.sum())}")
    print(f"invalid 개수: {int((~land_gdf.geometry.is_valid).sum())}")

    test_cases = [
        ("테스트 (내륙)", 36.97, 127.94, True),
        ("서울 (내륙)", 37.5665, 126.9780, True),
        ("안양 (내륙)", 37.3943, 126.9568, True),
        ("동해 (해상)", 36.5000, 131.0000, False),
        ("서울 경도 +360 정규화", 37.5665, 126.9780 + 360.0, True),
    ]

    print("\n=== 2. 기준 좌표 판정 검증 ===")
    all_passed = True
    for name, lat, lon, expected in test_cases:
        actual = is_on_land(lat, lon, land_gdf)
        passed = actual == expected
        all_passed = all_passed and passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: lat={lat}, lon={lon} -> actual={actual} (expected={expected})")

    if not all_passed:
        raise AssertionError("기준 좌표 검증 실패: 폴리곤 레이어 또는 버퍼 값을 점검하세요.")


if __name__ == "__main__":
    land_gdf_raw = load_land_polygons(buffer_meters=0.0)
    validate_land_gdf(land_gdf_raw)
