#!/usr/bin/env python3
"""Compile data/rankings.json + web/template.html into the leaderboard page.

Emits two files from one template:

  web/index.html     a complete standalone document — open it with file:// or
                     drop it on any static host, no build step, no server
  web/artifact.html  the same page as a body-only fragment, for publishing
                     through the Artifact tool (which supplies its own
                     <!doctype>/<head>/<body> wrapper)

The data is embedded rather than fetched so the page works from file:// — a
fetch of a sibling .json is blocked by CORS there.

Usage: python scripts/build_site.py
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from radiant_ingest.config import RAW_JSON_PATH  # noqa: E402
from radiant_rank.config import RANKINGS_PATH, school_label  # noqa: E402
from radiant_rank.tiers import TIERS, roll_up  # noqa: E402

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"
TEMPLATE_PATH = WEB_DIR / "template.html"
INDEX_PATH = WEB_DIR / "index.html"
ARTIFACT_PATH = WEB_DIR / "artifact.html"
RANK_ICON_DIR = WEB_DIR / "assets" / "ranks"


def tier_icon_data_uri(slug: str) -> str:
    """Inline a rank badge as a data URI.

    The Artifact host's CSP blocks images from every external origin, so a
    hotlinked badge renders as nothing at all, silently. Embedding is the only
    way these display — and it keeps web/index.html a genuinely single file.
    """
    path = RANK_ICON_DIR / f"{slug}.png"
    if not path.exists():
        raise SystemExit(
            f"missing rank badge {path} — re-run scripts/fetch_rank_icons.py"
        )
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

# Mirrors the reset the Artifact host injects, so both outputs render alike.
STANDALONE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { color-scheme: light dark; }
body { margin: 0; font: 14px system-ui, sans-serif; background: #fafafa; }
img { max-width: 100%; }
[hidden] { display: none !important; }
</style>
</head>
<body>
"""
STANDALONE_TAIL = "\n</body>\n</html>\n"


def build_payload() -> dict:
    """Pack the ranking into a compact, column-indexed payload.

    Records go out as positional arrays with department and school names
    interned, which takes the embedded data from ~2.6 MB of pretty JSON to a
    few hundred KB — worth it when it ships inside every page load.
    """
    ranking = json.loads(RANKINGS_PATH.read_text(encoding="utf-8"))
    meta = ranking["meta"]
    professors = ranking["professors"]
    if not professors:
        raise SystemExit("rankings.json has no professors — run `make rank` first")

    departments: list[str] = []
    dept_index: dict[str, int] = {}
    schools: list[dict] = []
    school_index: dict[str, int] = {}
    tier_index = {t.name: i for i, t in enumerate(TIERS)}

    rows = []
    for p in professors:
        dept = p["department"]
        if dept not in dept_index:
            dept_index[dept] = len(departments)
            departments.append(dept)

        sid = p["school_id"]
        if sid not in school_index:
            label = school_label(sid, p["school_name"])
            school_index[sid] = len(schools)
            schools.append(
                {
                    "id": sid,
                    "name": p["school_name"],
                    "label": label,
                    # Drives the chip colour; falls back to a neutral chip.
                    "cls": {"UNC": "unc", "Duke": "duke"}.get(label, "lead"),
                }
            )

        wta = p.get("would_take_again_percent")
        rows.append(
            [
                p["name"],                              # 0  NAME
                dept_index[dept],                       # 1  DEPT
                school_index[sid],                      # 2  SCHOOL
                round(p["elo"], 1),                     # 3  ELO
                round(p["elo_stddev"], 1),              # 4  SD
                round(p["avg_rating"], 1),              # 5  AVG
                p["num_ratings"],                       # 6  N
                round(wta, 1) if isinstance(wta, (int, float)) else -1,  # 7 WTA
                round(p["avg_difficulty"], 1) if p.get("avg_difficulty") else 0,  # 8 DIFF
                p["department_rank"],                   # 9  DRANK
                p["department_size"],                   # 10 DSIZE
                p["school_rank"],                       # 11 SRANK
                p["school_size"],                       # 12 SSIZE
                p["naive_rank"],                        # 13 NAIVE
                round(p["shrunk_rating"], 3),           # 14 SHRUNK
                tier_index[p["tier"]],                  # 15 TIER
            ]
        )

    # Total ratings is a property of the full pull, not just the ranked subset.
    raw = json.loads(RAW_JSON_PATH.read_text(encoding="utf-8"))
    total_ratings = sum(r.get("num_ratings") or 0 for r in raw)
    ingested_on = datetime.fromtimestamp(
        RAW_JSON_PATH.stat().st_mtime, tz=timezone.utc
    ).strftime("%d %B %Y")

    site_meta = {
        "ranked": meta["ranked"],
        "total_records": meta["total_records"],
        "min_ratings": meta["min_ratings"],
        "rounds": meta["rounds"],
        "tournaments": meta["tournaments"],
        "seed": meta["seed"],
        "prior_mean_rating": meta["prior_mean_rating"],
        "departments": len(departments),
        "total_ratings": total_ratings,
        "ingested_on": ingested_on,
        "spearman": (meta.get("stability") or {}).get("spearman"),
    }

    tier_rows = [dict(t, icon=tier_icon_data_uri(t["slug"])) for t in meta["tiers"]]

    return {
        "meta": site_meta,
        "departments": departments,
        "schools": schools,
        "tiers": tier_rows,
        "families": roll_up(meta["tiers"]),
        "rows": rows,
    }


def main() -> int:
    if not RANKINGS_PATH.exists():
        raise SystemExit(f"{RANKINGS_PATH} not found — run `make rank` first")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__PAYLOAD__" not in template:
        raise SystemExit(f"{TEMPLATE_PATH} is missing the __PAYLOAD__ placeholder")

    payload = build_payload()
    # separators drop the whitespace json.dumps would otherwise add per value.
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # The blob sits inside a <script> block, where a literal "</script>" in any
    # string would close it early. "<\/" is the same string to a JSON parser.
    blob = blob.replace("</", "<\\/")
    page = template.replace("__PAYLOAD__", blob)

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(page, encoding="utf-8")
    INDEX_PATH.write_text(STANDALONE_HEAD + page + STANDALONE_TAIL, encoding="utf-8")

    counts = Counter(r[2] for r in payload["rows"])
    print(f"  professors embedded : {len(payload['rows']):,}")
    for i, school in enumerate(payload["schools"]):
        print(f"    {school['label']:<6} {counts[i]:>6,}")
    print(f"  departments         : {len(payload['departments'])}")
    icon_bytes = sum(len(t["icon"]) for t in payload["tiers"])
    print(f"  rank badges         : {len(payload['tiers'])} ({icon_bytes / 1024:,.0f} KB inlined)")
    print(f"  payload             : {len(blob) / 1024:,.0f} KB")
    print(f"  {INDEX_PATH}  ({INDEX_PATH.stat().st_size / 1024:,.0f} KB)")
    print(f"  {ARTIFACT_PATH}  ({ARTIFACT_PATH.stat().st_size / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
