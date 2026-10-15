"""Download Utah reference boundaries from UGRC (SGID open data) into one GeoPackage.

Output: data/reference/utah_reference.gpkg (EPSG:26912, NAD83 / UTM 12N)
Layers: counties, health_districts, zip_codes, hssa_2020, municipalities, water

Pass layer names to fetch only those, e.g. `python scripts/01_fetch_reference.py water`.
"""
import sys
from pathlib import Path

import geopandas as gpd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reference" / "utah_reference.gpkg"
BASE = "https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/{}/FeatureServer/0/query"
CRS = "EPSG:26912"

LAYERS = {
    "counties": ("UtahCountyBoundaries", "NAME,FIPS_STR,POP_LASTCENSUS,POP_CURRESTIMATE"),
    "health_districts": ("HealthDistricts", "DISTNAME,ID_NUM"),
    "zip_codes": ("UtahZipCodeAreas", "ZIP5,NAME,COUNTYNBR"),
    "hssa_2020": ("HealthSmallStatisticalAreas2020", "SmallAreaNumber,SmallAreaName"),
    "municipalities": ("UtahMunicipalBoundaries", "NAME,POPLASTCENSUS"),
    # lakes, reservoirs and marshes (NHD), so member dots are kept off water; tiny ponds skipped
    "water": ("UtahLakesNHD", "GNIS_Name,FType_Text,AreaSqKm",
              "FType_Text IN ('Lake/Pond','Reservoir','Swamp/Marsh') AND AreaSqKm > 0.02"),
}


def fetch(service: str, fields: str, where: str = "1=1") -> gpd.GeoDataFrame:
    """Page through an ArcGIS FeatureServer layer and return it as a GeoDataFrame."""
    frames, offset = [], 0
    while True:
        r = requests.get(
            BASE.format(service),
            params={
                "where": where,
                "outFields": fields,
                "outSR": 4326,
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": 1000,
            },
            timeout=120,
        )
        r.raise_for_status()
        gj = r.json()
        feats = gj.get("features", [])
        if not feats:
            break
        frames.append(gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326"))
        if not gj.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += len(feats)
    gdf = gpd.GeoDataFrame(
        gpd.pd.concat(frames, ignore_index=True), crs="EPSG:4326"
    ).to_crs(CRS)
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for layer, (service, *query) in LAYERS.items():
        if sys.argv[1:] and layer not in sys.argv[1:]:
            continue
        gdf = fetch(service, *query)
        gdf.to_file(OUT, layer=layer, driver="GPKG")
        print(f"{layer:18s} {len(gdf):4d} features  <- {service}")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
