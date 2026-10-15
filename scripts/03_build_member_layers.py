"""Locate members by ZIP and build map layers.

The export has no street addresses, so the finest location is the member's ZIP code.
Members whose ZIP has no UGRC ZIP polygon (PO-box / unique ZIPs) fall back to their
city's municipal boundary. Current members with no ZIP at all are placed at their
employer/school (data/reference/company_locations.csv, hand-maintained) -- loc_method
"workplace" -- and counted separately (n_current_work) so maps can show or hide them.

Input:  data/processed/members_clean.csv, data/reference/utah_reference.gpkg
Output: data/processed/members.gpkg (EPSG:26912) with layers
  members_zip_points  one point per member, stacked at the ZIP's interior point
  members_dots        one point per member, randomly placed inside the ZIP
                      (inside its towns where possible, never on or within WATER_GAP of a
                      lake / reservoir / marsh) -- for dot-density display only.
                      Workplace-placed members are spread around their workplace instead.
  work_areas          the area each workplace's dots are spread over (only workplaces with more
                      than MIN_WORK_AREA members; smaller ones show as plain dots)
  town_counts         one point per town (member-reported city) with member counts
  zip_counts          ZIP polygons with member counts
  county_counts       counties with counts and members per 10k residents
  lhd_counts          local health districts with counts and members per 10k residents
  hssa_counts         Health Small Statistical Areas (2020) with counts
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference" / "utah_reference.gpkg"
COMPANIES = ROOT / "data" / "reference" / "company_locations.csv"
CLEAN = ROOT / "data" / "processed" / "members_clean.csv"
OUT = ROOT / "data" / "processed" / "members.gpkg"
SEED = 2026
WORK_AREA_PER_DOT = 200_000  # m^2 of spread area per workplace-placed member (~350 m spacing)
WATER_GAP = 150  # m; dots stay at least this far from lakes so they don't sit on the shoreline
MIN_WORK_AREA = 3  # workplaces with this many members or fewer get dots only (no dashed circle)

KEEP = ["member_id", "member_group", "membership_type", "status", "join_year", "joined_date",
        "expiration_date", "company", "city", "zip5", "board_interest"]


def ref(layer):
    return gpd.read_file(REF, layer=layer)


def random_point_in(poly, rng, water=None, tries=5000):
    """Random point inside poly, off the water (an STRtree of buffered lakes) when possible."""
    minx, miny, maxx, maxy = poly.bounds
    fallback = None
    for _ in range(tries):
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if not poly.contains(p):
            continue
        if water is None or not len(water.query(p, predicate="intersects")):
            return p
        if fallback is None:
            fallback = p
    return fallback if fallback is not None else poly.representative_point()


def counts_by(members, key):
    work = members["loc_method"] == "workplace"
    flags = pd.DataFrame({
        key: members[key],
        "n_members": 1,
        "n_current": (members["member_group"] == "current") & ~work,
        "n_current_work": work,
        "n_former": members["member_group"] == "former",
    })
    return flags.groupby(key).sum().astype(int)


def attach_counts(polys, members, key, pop_col=None):
    out = polys.merge(counts_by(members, key), left_on=key, right_index=True, how="left")
    for c in ["n_members", "n_current", "n_current_work", "n_former"]:
        out[c] = out[c].fillna(0).astype(int)
    if pop_col:
        out["per10k_members"] = (out["n_members"] / out[pop_col] * 1e4).round(2)
        out["per10k_current"] = (out["n_current"] / out[pop_col] * 1e4).round(2)
        out["per10k_current_all"] = ((out["n_current"] + out["n_current_work"])
                                     / out[pop_col] * 1e4).round(2)
    return out


def main():
    rng = np.random.default_rng(SEED)
    m = pd.read_csv(CLEAN, dtype={"zip5": str})
    # current members with no ZIP -> employer / school location
    comp = pd.read_csv(COMPANIES, dtype={"zip5": str}).dropna(subset=["town"])
    comp = comp.set_index("company")
    m["loc_zip"], m["loc_city"], m["org"] = m["zip5"], m["city"], pd.NA
    m["org_lat"], m["org_lon"] = float("nan"), float("nan")
    work = (m["member_group"] == "current") & ~m["has_zip"] & m["company"].isin(comp.index)
    m.loc[work, "loc_zip"] = m.loc[work, "company"].map(comp["zip5"])
    m.loc[work, "loc_city"] = m.loc[work, "company"].map(comp["town"])
    m.loc[work, "org"] = m.loc[work, "company"].map(comp["org"])
    m.loc[work, "org_lat"] = m.loc[work, "company"].map(comp["lat"]).astype(float)
    m.loc[work, "org_lon"] = m.loc[work, "company"].map(comp["lon"]).astype(float)
    m = m[m["in_utah"] | work][KEEP + ["loc_zip", "loc_city", "org", "org_lat", "org_lon"]].copy()
    m["is_work"] = work[m.index]

    zips = ref("zip_codes").dissolve(by="ZIP5", as_index=False, aggfunc="first")
    zips = zips.rename(columns={"NAME": "zip_name"})[["ZIP5", "zip_name", "geometry"]]
    munis = ref("municipalities")
    counties = ref("counties").rename(columns={"NAME": "county", "POP_CURRESTIMATE": "pop"})
    lhds = ref("health_districts").rename(columns={"DISTNAME": "lhd"})
    hssa = ref("hssa_2020")

    # --- locate each member -------------------------------------------------
    zip_geom = zips.set_index("ZIP5").geometry
    muni_geom = munis.assign(key=munis["NAME"].str.title()).dissolve(by="key").geometry
    # where the ZIP overlaps towns, scatter dots there instead of across open land
    town_union = munis.unary_union
    # lakes, reservoirs and marshes, widened by WATER_GAP; dots are redrawn if they land on one
    water = STRtree(ref("water").to_crs(zips.crs).geometry.buffer(WATER_GAP).values)

    pts, dots, method = [], [], []
    for z, city, is_work in zip(m["loc_zip"], m["loc_city"], m["is_work"]):
        if z in zip_geom.index:
            area = zip_geom[z]
            method.append("workplace" if is_work else "zip_polygon")
        elif isinstance(city, str) and city in muni_geom.index:
            area = muni_geom[city]
            method.append("city_fallback")
        else:
            pts.append(None); dots.append(None); method.append("unlocated")
            continue
        pts.append(area.representative_point())
        towns = area.intersection(town_union)
        dots.append(random_point_in(towns if towns.area > 0.05 * area.area or towns.area > 2e6
                                    else area, rng, water))

    m["loc_method"] = method

    # Workplace-placed members: spread their dots around the workplace (its geocoded address in
    # company_locations.csv) instead of inside its ZIP (campus ZIPs like USU's are tiny, so dots
    # would pile up). The area is a circle around the workplace, clipped to town land, grown until
    # it allows ~WORK_AREA_PER_DOT per member.
    work_areas = []
    work_pos = [i for i, w in enumerate(m["is_work"]) if w]
    wk = m.iloc[work_pos]
    centers = gpd.GeoSeries(gpd.points_from_xy(wk["org_lon"], wk["org_lat"]), crs=4326).to_crs(zips.crs)
    keys = [f"{g.x:.0f},{g.y:.0f}" for g in centers]
    for key, idx in pd.Series(work_pos).groupby(keys):
        idx = list(idx)
        center = centers.iloc[work_pos.index(idx[0])]
        z = m["loc_zip"].iloc[idx[0]]
        r = 800.0
        while True:
            disk = center.buffer(r)
            region = disk.intersection(town_union)
            if region.area < 0.3 * disk.area:          # mostly open land: use the plain circle
                region = disk
            if region.area >= len(idx) * WORK_AREA_PER_DOT:
                break
            r *= 1.2
        for i in idx:
            dots[i] = random_point_in(region, rng, water)
        if len(idx) <= MIN_WORK_AREA:                  # small groups: just dots, no outlined area
            continue
        orgs = m["org"].iloc[idx].value_counts()
        work_areas.append({"loc_zip": z, "n": len(idx),
                           "orgs": "; ".join(f"{o} ({n})" for o, n in orgs.items()),
                           "geometry": region})
    work_areas = gpd.GeoDataFrame(work_areas, crs=zips.crs)
    located = m["loc_method"] != "unlocated"
    pts_gdf = gpd.GeoDataFrame(m[located], geometry=[p for p in pts if p is not None],
                               crs=zips.crs)

    # admin areas from the stacked point
    pts_gdf = (pts_gdf
               .sjoin(counties[["county", "FIPS_STR", "geometry"]], how="left", predicate="within")
               .drop(columns="index_right")
               .sjoin(lhds[["lhd", "geometry"]], how="left", predicate="within")
               .drop(columns="index_right")
               .sjoin(hssa[["SmallAreaNumber", "SmallAreaName", "geometry"]], how="left",
                      predicate="within")
               .drop(columns="index_right"))
    dots_gdf = pts_gdf.copy().set_geometry([d for d in dots if d is not None], crs=zips.crs)

    # --- aggregates ----------------------------------------------------------
    zip_counts = attach_counts(zips, pts_gdf.rename(columns={"loc_zip": "ZIP5"}), "ZIP5")

    # towns: the member-reported city (falls back to the ZIP's postal name). Point goes at the
    # municipality's interior point, or for unincorporated places at the middle of their ZIPs.
    pts_gdf = pts_gdf.merge(zips[["ZIP5", "zip_name"]], left_on="loc_zip", right_on="ZIP5",
                            how="left").drop(columns="ZIP5")
    # Members often give a more specific town than the ZIP's postal name (Cottonwood Heights vs
    # Salt Lake City), so trust the reported city unless it is >5 km from the ZIP (a typo).
    postal = pts_gdf["zip_name"].str.title().replace({"Saint George": "St. George"})

    def pick_town(city, z, fallback):
        if not isinstance(city, str):
            return fallback
        if (isinstance(fallback, str) and city in muni_geom.index and z in zip_geom.index
                and muni_geom[city].distance(zip_geom[z]) > 5000):
            return fallback
        return city

    pts_gdf["town"] = [pick_town(c, z, f) for c, z, f in
                       zip(pts_gdf["loc_city"], pts_gdf["loc_zip"], postal)]
    town_rows = []
    for town, grp in pts_gdf.groupby("town"):
        if town in muni_geom.index:
            geom = muni_geom[town].representative_point()
        else:
            areas = [zip_geom[z] for z in grp["loc_zip"].unique() if z in zip_geom.index]
            geom = (gpd.GeoSeries(areas).unary_union.representative_point() if areas
                    else grp.geometry.iloc[0])
        orgs = grp["org"].dropna().value_counts()
        town_rows.append({"town": town, "zips": ", ".join(sorted(grp["loc_zip"].unique())),
                          "lhd": grp["lhd"].mode().iloc[0],
                          "work_orgs": "; ".join(f"{o} ({n})" for o, n in orgs.items()),
                          "geometry": geom})
    towns_gdf = gpd.GeoDataFrame(town_rows, crs=zips.crs)
    town_counts = attach_counts(towns_gdf, pts_gdf, "town")
    county_counts = attach_counts(counties[["county", "FIPS_STR", "pop", "geometry"]],
                                  pts_gdf, "county", "pop")
    # LHDs follow county lines; population = sum of member counties
    c2l = counties.assign(geometry=counties.representative_point()).sjoin(
        lhds[["lhd", "geometry"]], predicate="within")
    lhd_pop = c2l.groupby("lhd").agg(pop=("pop", "sum"),
                                     counties=("county", lambda s: ", ".join(sorted(s))))
    lhd_counts = attach_counts(lhds[["lhd", "geometry"]].merge(lhd_pop, on="lhd"),
                               pts_gdf, "lhd", "pop")
    hssa_counts = attach_counts(hssa, pts_gdf, "SmallAreaNumber")

    # --- write ---------------------------------------------------------------
    OUT.unlink(missing_ok=True)
    for name, gdf in [("members_zip_points", pts_gdf), ("members_dots", dots_gdf),
                      ("town_counts", town_counts), ("zip_counts", zip_counts), ("county_counts", county_counts),
                      ("lhd_counts", lhd_counts), ("hssa_counts", hssa_counts),
                      ("work_areas", work_areas)]:
        gdf.to_file(OUT, layer=name, driver="GPKG")

    print(f"Members placed on map: {len(m)}")
    print(m["loc_method"].value_counts().to_string())
    print(f"Wrote {OUT}\n")
    print(lhd_counts.drop(columns="geometry").sort_values("n_members", ascending=False)
          .to_string(index=False))


if __name__ == "__main__":
    main()
