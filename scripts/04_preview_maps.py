"""Quick-look PNG maps (QGIS layouts are the polished versions).

Output: outputs/maps/preview_*.png
"""
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt

from org_settings import load

ROOT = Path(__file__).resolve().parents[1]
G = ROOT / "data" / "processed" / "members.gpkg"
R = ROOT / "data" / "reference" / "utah_reference.gpkg"
OUT = ROOT / "outputs" / "maps"
ORG = load()["short_name"]
WASATCH = (395000, 4440000, 465000, 4600000)  # UTM 12N: Provo to Ogden/Logan edge


def base(ax, counties):
    counties.boundary.plot(ax=ax, color="#888", linewidth=0.4)
    ax.set_axis_off()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    counties = gpd.read_file(R, layer="counties")
    lhd = gpd.read_file(G, layer="lhd_counts")
    cty = gpd.read_file(G, layer="county_counts")
    zips = gpd.read_file(G, layer="zip_counts")
    dots = gpd.read_file(G, layer="members_dots")
    n = len(dots)

    # 1. LHD: members per 10k residents
    fig, ax = plt.subplots(figsize=(7, 8.5))
    lhd.plot(column="per10k_members", cmap="Blues", ax=ax, edgecolor="white", linewidth=0.8,
             legend=True, legend_kwds={"label": "Members per 10,000 residents", "shrink": 0.6})
    for r in lhd.itertuples():
        x, y = r.geometry.representative_point().coords[0]
        ax.annotate(f"{r.lhd}\n{r.n_members}", (x, y), ha="center", fontsize=6.5)
    ax.set_axis_off()
    ax.set_title(f"{ORG} members by Local Health District (n={n} with a Utah ZIP)", fontsize=10)
    fig.savefig(OUT / "preview_lhd_per10k.png", dpi=200, bbox_inches="tight")

    # 2. County: raw count
    fig, ax = plt.subplots(figsize=(7, 8.5))
    cty.plot(column="n_members", cmap="Purples", ax=ax, edgecolor="white", linewidth=0.6,
             legend=True, legend_kwds={"label": "Members", "shrink": 0.6})
    ax.set_axis_off()
    ax.set_title(f"{ORG} members by county", fontsize=10)
    fig.savefig(OUT / "preview_county_counts.png", dpi=200, bbox_inches="tight")

    # 3. Statewide dots + Wasatch Front inset, ZIP choropleth underneath
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 8), gridspec_kw={"width_ratios": [1, 1]})
    for ax, ext in [(a1, None), (a2, WASATCH)]:
        zips[zips.n_members > 0].plot(column="n_members", cmap="Greens", ax=ax, alpha=0.6,
                                      edgecolor="#bbb", linewidth=0.2)
        base(ax, counties)
        for status, color in [("former", "#999999"), ("current", "#c0392b")]:
            dots[dots.member_group == status].plot(ax=ax, markersize=4 if ext else 2, color=color,
                                             label=status)
        dots[~dots.member_group.isin(["current", "former"])].plot(ax=ax, markersize=4 if ext else 2,
                                                            color="#2c7fb8", label="Other")
        if ext:
            ax.set_xlim(ext[0], ext[2]); ax.set_ylim(ext[1], ext[3])
    a1.add_patch(plt.Rectangle(WASATCH[:2], WASATCH[2] - WASATCH[0], WASATCH[3] - WASATCH[1],
                               fill=False, edgecolor="black", linewidth=0.8))
    a2.legend(loc="lower right", fontsize=8, title="Member group")
    a1.set_title("Statewide", fontsize=10); a2.set_title("Wasatch Front", fontsize=10)
    fig.suptitle(f"{ORG} members (dots placed randomly within each member's ZIP code)", fontsize=11)
    fig.savefig(OUT / "preview_dots_wasatch.png", dpi=200, bbox_inches="tight")
    print("Wrote previews to", OUT)


if __name__ == "__main__":
    main()
