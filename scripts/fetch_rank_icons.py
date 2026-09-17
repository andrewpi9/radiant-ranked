#!/usr/bin/env python3
"""Fetch VALORANT competitive rank badges into web/assets/ranks/.

Pulls the current competitive tier table from valorant-api.com (a community
mirror of Riot's own game assets) and saves the large icon for each of the nine
main tiers. The badges are then vendored in the repo so `make site` never needs
the network, and re-running this refreshes them if Riot reissues the artwork.

Riot's official per-tier colours live in radiant_rank/tiers.py; this script
reports any drift between the two rather than silently disagreeing.

Usage: python scripts/fetch_rank_icons.py
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from radiant_rank.tiers import TIERS  # noqa: E402

API = "https://valorant-api.com/v1/competitivetiers"
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "web" / "assets" / "ranks"

# Riot ships these at 256px; the page shows them at 26-42px. All 25 at full size
# would add ~860 KB of base64 to every page load for no visible gain, so they
# are downscaled to a size that still covers a 2x display.
TARGET_PX = 96


def downscale(path: pathlib.Path) -> None:
    """Shrink in place, preserving alpha. No-op if no resizer is available."""
    if shutil.which("sips"):  # macOS, no dependency to install
        subprocess.run(
            ["sips", "-Z", str(TARGET_PX), str(path)],
            check=True, capture_output=True,
        )
        return
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(path) as img:
        img.convert("RGBA").resize((TARGET_PX, TARGET_PX), Image.LANCZOS).save(path)


def main() -> int:
    resp = requests.get(API, timeout=30)
    resp.raise_for_status()
    # The last tier set is the current episode's.
    by_id = {t["tier"]: t for t in resp.json()["data"][-1]["tiers"]}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    drifted = 0
    for tier in TIERS:
        node = by_id.get(tier.api_tier)
        if not node or not node.get("largeIcon"):
            print(f"  !! no icon for {tier.name} (api tier {tier.api_tier})")
            continue

        blob = requests.get(node["largeIcon"], timeout=30).content
        path = OUT_DIR / f"{tier.slug}.png"
        path.write_bytes(blob)
        downscale(path)
        size = path.stat().st_size
        total += size

        official = "#" + str(node.get("color", ""))[:6].lower()
        drift = ""
        if official != tier.color.lower():
            drift = f"  <- COLOUR DRIFT, api says {official}"
            drifted += 1
        print(
            f"  {tier.name:<12} {len(blob) / 1024:>6.1f} KB -> {size / 1024:>5.1f} KB"
            f"  {tier.color}{drift}"
        )

    print(f"  {'total':<12} {total / 1024:>6.1f} KB across {len(TIERS)} divisions -> {OUT_DIR}")
    if drifted:
        print(f"  !! {drifted} colour(s) drifted — update radiant_rank/tiers.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
