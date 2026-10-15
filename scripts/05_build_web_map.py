"""Build the interactive member map pages (MapLibre, single self-contained HTML files).

Two versions from the same template:
  internal  outputs/web/<internal_html>  (git-ignored; board and staff only; named in org_settings)
            aggregate counts per ZIP / county, plus one randomly placed dot per member and the
            workplace areas (organization names). No member rows.
  public    docs/index.html              (published with the repo / GitHub Pages)
            current members by ZIP code ONLY -- no dots, no counties, no workplace names, no former
            or pending breakdowns, and a generic title (no organization name). Any ZIP count from 1 to
            MIN_CELL - 1 is replaced by "fewer than MIN_CELL" before it is written, so the exact small
            numbers never reach the page.

Input:  data/processed/members_clean.csv, data/processed/members.gpkg
"""
import html
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient

from org_settings import PUBLIC_TITLE, load

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "processed" / "members_clean.csv"
G = ROOT / "data" / "processed" / "members.gpkg"
TEMPLATE = Path(__file__).with_name("web_map_template.html")
ORG = load()
OUT = ROOT / "outputs" / "web" / ORG["internal_html"]
PUBLIC_OUT = ROOT / "docs" / "index.html"
MIN_CELL = 5  # public page: counts of 1-4 are shown only as "fewer than 5"
AS_OF = "October 5, 2026"  # keep in sync with AS_OF in 02_clean_members.py


def geojson(gdf, cols, simplify=None):
    gdf = gdf[cols + ["geometry"]].copy()
    if simplify:
        gdf["geometry"] = gdf.geometry.simplify(simplify, preserve_topology=True)
    gdf = gdf.to_crs(4326)
    gj = json.loads(gdf.to_json(drop_id=True))
    # trim coordinate precision (~1 m) to keep the page small
    def rnd(x):
        return [rnd(v) for v in x] if isinstance(x, list) else round(x, 5)
    for f in gj["features"]:
        f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
    return gj


def county_map(counties, width=330):
    """Static SVG of Utah counties (colored in the page) and the per-county numbers behind it."""
    c = counties.copy()
    c["geometry"] = c.geometry.simplify(300, preserve_topology=True)
    minx, miny, maxx, maxy = c.total_bounds
    k = width / (maxx - minx)
    height = (maxy - miny) * k

    def ring(coords):
        return "M" + "L".join(f"{(x - minx) * k:.1f},{(maxy - y) * k:.1f}" for x, y in coords) + "Z"

    paths = []
    for r in c.itertuples():
        polys = getattr(r.geometry, "geoms", [r.geometry])
        d = "".join(ring(pg.exterior.coords) + "".join(ring(h.coords) for h in pg.interiors) for pg in polys)
        paths.append(f'<path data-fips="{r.FIPS_STR}" d="{d}" fill-rule="evenodd"/>')
    # the page draws this SVG twice (members per 10k, and expected members), so no ids inside
    svg = f'<svg viewBox="0 0 {width} {height:.0f}" role="img">{"".join(paths)}</svg>'
    table = [{"fips": r.FIPS_STR, "county": r.county.title(), "pop": int(r.pop),
              "home": int(r.n_current), "work": int(r.n_current_work)} for r in c.itertuples()]
    return svg, table


def stats(m, pts):
    m = m.merge(pts[["member_id", "loc_method"]], on="member_id", how="left")
    m["home"] = m["loc_method"].isin(["zip_polygon", "city_fallback"])
    m["work"] = m["loc_method"].eq("workplace")
    m["oos"] = m["has_zip"] & ~m["in_utah"]
    m["unlocated"] = ~(m["home"] | m["work"] | m["oos"])
    out = {}
    for grp in ["current", "former", "pending"]:
        g = m[m.member_group == grp]
        out[grp] = {"total": len(g), **{k: int(g[k].sum())
                                         for k in ["home", "work", "oos", "unlocated"]}}
    cur = m[m.member_group == "current"]
    by_type = (cur.groupby("membership_type")[["home", "work", "unlocated"]].sum().astype(int)
               .assign(total=cur.membership_type.value_counts())
               .sort_values("total", ascending=False).reset_index())
    out["current_by_type"] = by_type.to_dict("records")
    return out


def main():
    m = pd.read_csv(CLEAN, dtype={"zip5": str})
    zips = gpd.read_file(G, layer="zip_counts")
    counties = gpd.read_file(G, layer="county_counts")
    pts = gpd.read_file(G, layer="members_zip_points", ignore_geometry=True)
    dots = gpd.read_file(G, layer="members_dots").to_crs(4326)
    # one dot per member: [lon, lat, category]; 0 = current (home ZIP), 1 = current (placed at
    # workplace), 2 = former. Only the category is embedded -- no member attributes.
    cat = (dots["loc_method"].eq("workplace").map({True: 1, False: 0})
           .where(dots["member_group"] == "current", 2))
    dot_rows = [[round(g.x, 4), round(g.y, 4), int(c)] for g, c in zip(dots.geometry, cat)]
    work_areas = gpd.read_file(G, layer="work_areas")

    data = {
        "asOf": AS_OF,
        "stats": stats(m, pts),
        "dots": dot_rows,
        "workAreas": geojson(work_areas, ["n", "orgs"], simplify=50),
        # every ZIP area, shaded by its member count on the page (no ZIP populations, so no rates)
        "zips": geojson(zips.assign(
            zip_name=zips["zip_name"].str.title(),
            lx=zips.geometry.representative_point().to_crs(4326).x.round(4),
            ly=zips.geometry.representative_point().to_crs(4326).y.round(4)),
            ["ZIP5", "zip_name", "n_current", "n_current_work", "n_former", "lx", "ly"], simplify=100),
        # members whose ZIP has no boundary (PO-box ZIPs) were placed by city; not in any ZIP area
        "noZipArea": pts.loc[pts["loc_method"] == "city_fallback", "member_group"]
                        .value_counts().to_dict(),
        "areas": geojson(counties.assign(
            name=counties["county"].str.title(),
            lx=counties.geometry.representative_point().to_crs(4326).x.round(4),
            ly=counties.geometry.representative_point().to_crs(4326).y.round(4)),
            ["name", "pop", "n_current", "n_current_work", "n_former", "lx", "ly"], simplify=100),
    }
    data["countySvg"], data["countyTable"] = county_map(counties)
    # grey-out mask: the whole world with a Utah-shaped hole, plus Utah's outline
    utah = Polygon(counties.geometry.unary_union.exterior).simplify(100)
    utah = gpd.GeoSeries([utah], crs=counties.crs).to_crs(4326)
    data["outsideUtah"] = geojson(gpd.GeoDataFrame(
        geometry=[orient(box(-179, -85, 179, 85).difference(utah.iloc[0]))], crs=4326), [])
    data["utahLine"] = geojson(gpd.GeoDataFrame(geometry=utah.boundary, crs=4326), [])
    # feature ids let the live map highlight the hovered county / ZIP
    for key in ["areas", "zips"]:
        for i, f in enumerate(data[key]["features"]):
            f["id"] = i
    write(OUT, data, ORG["internal_title"])
    write(PUBLIC_OUT, public_data(data), PUBLIC_TITLE)
    print(f"current: {data['stats']['current']}")


def suppress(n):
    """Public page: exact counts of 1 to MIN_CELL - 1 are withheld (None = 'fewer than MIN_CELL')."""
    n = int(n)
    return None if 0 < n < MIN_CELL else n


def public_data(data):
    """Aggregate-only copy of the page data: current members (home ZIP + workplace) per ZIP code,
    small counts suppressed; no county data at all. Built by whitelisting fields, so nothing new
    leaks by default."""
    def area_props(p, keep):
        return {**{k: p[k] for k in keep}, "n_current": suppress(p["n_current"] + p["n_current_work"]),
                "n_current_work": 0, "n_former": 0}

    def areas(fc, keep):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": f["id"], "geometry": f["geometry"],
             "properties": area_props(f["properties"], keep)} for f in fc["features"]]}

    s = data["stats"]
    return {
        "public": True,
        "minCell": MIN_CELL,
        "asOf": data["asOf"],
        # statewide totals only; no former / pending / membership-type breakdowns
        "stats": {"current": s["current"]},
        "dots": [],
        "workAreas": {"type": "FeatureCollection", "features": []},
        "zips": areas(data["zips"], ["ZIP5", "zip_name", "lx", "ly"]),
        "noZipArea": {"current": suppress(data["noZipArea"].get("current", 0))},
        "areas": {"type": "FeatureCollection", "features": []},      # no counties view
        "outsideUtah": data["outsideUtah"],
        "utahLine": data["utahLine"],
    }


def write(path, data, title):
    path.parent.mkdir(parents=True, exist_ok=True)
    page = (TEMPLATE.read_text(encoding="utf-8").replace("__PAGE_TITLE__", html.escape(title))
            .replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))))
    path.write_text(page, encoding="utf-8")
    print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
