"""Clean the raw member export.

Input:  the member export in data/raw/ (read-only; see org_settings.raw_export)
Output: data/processed/members_clean.csv

- drops the field-description row the export puts under the header
- adds a stable member_id (row order of the raw file)
- normalizes ZIP to 5 digits, city to title case
- parses dates; adds join_year
- drops the empty facebook column and any street-address columns, renames the board-interest
  survey column
- flags has_zip / in_utah
- assigns member_group:
    current  = AS_OF falls between joined_date and expiration_date, OR status is Active
               (many Active members -- Life, Student, etc. -- have no expiration date)
    former   = Expired / Cancelled and not current
    pending  = Pending / Prospective (never completed membership; no locations in export)
"""
from pathlib import Path

import pandas as pd

from org_settings import raw_export

AS_OF = pd.Timestamp("2026-10-05")  # date "currently active" is judged against

ROOT = Path(__file__).resolve().parents[1]
RAW = raw_export()
OUT = ROOT / "data" / "processed" / "members_clean.csv"

CITY_FIXES = {
    "Slc": "Salt Lake City", "Salt Lake": "Salt Lake City", "Salt Lakecity": "Salt Lake City",
    "Salt Lake Cty": "Salt Lake City", "Saint George": "St. George", "St George": "St. George",
    "West Valley": "West Valley City", "Farrwest": "Farr West",
}


def main():
    df = pd.read_excel(RAW, dtype=str)
    # Row 0 is a description of each field, not a member
    df = df[df["status"] != "Status of a Membership"]
    # a record with company "TEST" is a system test entry, not a member
    df = df[df["company"].str.strip().ne("TEST") | df["company"].isna()].reset_index(drop=True)
    df.insert(0, "member_id", range(1, len(df) + 1))

    # Never carry street addresses forward: maps place members within their ZIP, not at home.
    # (The current export has none; this guards a future one that does.)
    addr = [c for c in df.columns if any(k in c.lower() for k in ("address", "street", "addr"))]
    df = df.drop(columns=["facebook"] + addr).rename(
        columns={"kqjojhyE783fgsQin": "board_interest", "region": "state"}
    )
    for c in df.columns.drop("member_id"):
        df[c] = df[c].str.strip().replace("", pd.NA)

    df["zip5"] = df["zip"].str.extract(r"^(\d{5})", expand=False)
    df["city"] = (
        df["city"].str.title()
        .str.replace(r",.*$", "", regex=True)          # "Tooele, Ut, 84074" -> "Tooele"
        .str.replace(r"\s+Utah$", "", regex=True)       # "Highland Utah"
        .replace(CITY_FIXES)
    )
    df["state"] = df["state"].str.upper()

    for c in ["joined_date", "expiration_date", "rebilling_date"]:
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
    df["join_year"] = pd.to_datetime(df["joined_date"]).dt.year.astype("Int64")

    joined = pd.to_datetime(df["joined_date"])
    expires = pd.to_datetime(df["expiration_date"])
    in_range = (joined <= AS_OF) & (expires >= AS_OF)
    df["member_group"] = "pending"
    df.loc[df["status"].isin(["Expired", "Cancelled"]), "member_group"] = "former"
    df.loc[in_range | df["status"].eq("Active"), "member_group"] = "current"

    df["has_zip"] = df["zip5"].notna()
    df["in_utah"] = df["has_zip"] & df["zip5"].str.startswith("84") & df["state"].fillna("UT").eq("UT")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["zip"]).to_csv(OUT, index=False)

    print(f"{len(df)} members -> {OUT}")
    print(f"  with ZIP: {df.has_zip.sum()}  Utah ZIP: {df.in_utah.sum()}  "
          f"out of state: {(df.has_zip & ~df.in_utah).sum()}")
    print(df.groupby("member_group")[["has_zip", "in_utah"]].sum()
          .assign(total=df.member_group.value_counts()))


if __name__ == "__main__":
    main()
