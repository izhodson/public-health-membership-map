"""Organization-specific names, kept out of the public repository.

They are read from data/private/org.json (git-ignored). Without that file the scripts use generic
names, so neither the code nor the public map names the organization.

data/private/org.json keys (all optional):
  short_name       used in preview-image titles, e.g. "ABC" -> "ABC members by county"
  raw_file         member export file name inside data/raw/ (default: the only .xlsx there)
  internal_title   title of the internal map page
  internal_html    file name of the internal map page in outputs/web/
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data" / "private" / "org.json"
PUBLIC_TITLE = "Map for Local Public Health Organization Membership"
DEFAULTS = {
    "short_name": "Organization",
    "raw_file": None,
    "internal_title": "Member Map (internal)",
    "internal_html": "member_map_internal.html",
}


def load():
    settings = dict(DEFAULTS)
    if PRIVATE.exists():
        settings.update(json.loads(PRIVATE.read_text(encoding="utf-8")))
    return settings


def raw_export():
    """Path of the member export in data/raw/."""
    name = load()["raw_file"]
    if name:
        return ROOT / "data" / "raw" / name
    found = sorted((ROOT / "data" / "raw").glob("*.xlsx"))
    if len(found) != 1:
        raise FileNotFoundError("Put exactly one .xlsx in data/raw/, or set raw_file in data/private/org.json")
    return found[0]
