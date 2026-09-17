"""Post-pull validation.

The point of this module is to make a broken query *look* broken. An
undocumented API can keep returning HTTP 200 while quietly handing back
truncated pages or nulled-out fields, so nothing downstream should trust the
dataset until these counts have been eyeballed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Optional, Sequence

from . import config
from .persistence import Checkpoint

RULE = "=" * 72
THIN = "-" * 72


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "n/a"


def _school_totals(
    stats: Optional[Sequence[dict[str, Any]]],
) -> dict[str, dict[str, Optional[int]]]:
    """Per-school ``{rows, result_count}`` from this run, else from the checkpoint.

    ``rows`` is what the API actually handed us before dedup. RMP serves some
    teachers twice, so ``rows`` — not the deduped record count — is what should
    equal ``resultCount``. Comparing the deduped count instead makes a complete
    pull look truncated.
    """
    totals: dict[str, dict[str, Optional[int]]] = {}
    if stats:
        for stat in stats:
            name = stat["school"].get("name")
            if name:
                totals[name] = {
                    "rows": stat.get("fetched"),
                    "result_count": stat.get("result_count"),
                }
    if not totals:
        for entry in Checkpoint.load().state.get("schools", {}).values():
            if entry.get("name"):
                totals[entry["name"]] = {
                    "rows": entry.get("fetched"),
                    "result_count": entry.get("result_count"),
                }
    return totals


def validate(
    records: Sequence[dict[str, Any]],
    *,
    stats: Optional[Sequence[dict[str, Any]]] = None,
    samples: int = 3,
) -> bool:
    """Print the validation report. Returns True if nothing needs attention."""
    problems: list[str] = []
    warnings: list[str] = []

    by_school: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_school[rec.get("school_name") or "<unknown>"].append(rec)

    totals = _school_totals(stats)

    print()
    print(RULE)
    print(" INGESTION VALIDATION")
    print(RULE)

    # ---- counts per school -------------------------------------------------
    # `rows` vs `resultCount` proves pagination reached the end; `unique` vs
    # `rows` just reports how many duplicates RMP served us.
    print("\nCounts by school")
    print(f"  {'School':<44}{'unique':>8}{'rows':>8}{'API says':>10}{'delta':>7}")
    print(f"  {THIN[:73]}")
    total_rows = 0
    for name in sorted(by_school):
        unique = len(by_school[name])
        info = totals.get(name) or {}
        rows = info.get("rows")
        exp = info.get("result_count")
        total_rows += rows or unique

        delta = "" if (rows is None or exp is None) else f"{rows - exp:+d}"
        print(
            f"  {name[:43]:<44}{unique:>8}"
            f"{(rows if rows is not None else '?'):>8}"
            f"{(exp if exp is not None else '?'):>10}{delta:>7}"
        )

        if unique == 0:
            problems.append(f"{name}: pulled 0 records")
        elif exp and rows is not None and abs(rows - exp) / exp > config.COUNT_TOLERANCE:
            problems.append(
                f"{name}: fetched {rows} rows but API reported {exp} "
                f"({_pct(abs(rows - exp), exp)} off) — pagination may have truncated"
            )
    print(f"  {THIN[:73]}")
    print(f"  {'TOTAL':<44}{len(records):>8}{total_rows:>8}")

    if not records:
        problems.append("no records at all")

    # ---- hard data-quality flags ------------------------------------------
    null_avg = [r for r in records if r.get("avg_rating") is None]
    null_num = [r for r in records if r.get("num_ratings") is None]
    null_id = [r for r in records if not r.get("id")]
    no_last_name = [r for r in records if not r.get("last_name")]
    no_school_tag = [r for r in records if not r.get("school_id") or not r.get("school_name")]
    rated_no_avg = [
        r for r in records if (r.get("num_ratings") or 0) > 0 and not r.get("avg_rating")
    ]

    id_counts = Counter(r.get("id") for r in records if r.get("id"))
    dupes = [rid for rid, n in id_counts.items() if n > 1]

    checks = [
        ("null/missing avg_rating", len(null_avg), True),
        ("null/missing num_ratings", len(null_num), True),
        ("null/missing professor id", len(null_id), True),
        ("duplicate professor ids", len(dupes), True),
        ("missing school_id/school_name", len(no_school_tag), True),
        ("rated but avg_rating is 0/null", len(rated_no_avg), True),
        ("missing last_name", len(no_last_name), False),
        ("missing department", len([r for r in records if not r.get("department")]), False),
    ]

    print("\nData quality flags")
    for label, count, is_error in checks:
        tag = "ok  " if count == 0 else ("FAIL" if is_error else "warn")
        print(f"  [{tag}] {label:.<52} {count}")
        if count:
            (problems if is_error else warnings).append(f"{label}: {count}")

    # ---- expected sentinels, informational only ---------------------------
    unrated = [r for r in records if (r.get("num_ratings") or 0) == 0]
    rated = [r for r in records if (r.get("num_ratings") or 0) > 0]
    no_wta = [r for r in records if (r.get("would_take_again_percent") or -1) < 0]
    total_ratings = sum(r.get("num_ratings") or 0 for r in records)

    print("\nInformational (expected RMP sentinels, not errors)")
    print(f"  professors with 0 ratings {'.' * 27} {len(unrated)} ({_pct(len(unrated), len(records))})")
    print(f"  professors with >=1 rating {'.' * 26} {len(rated)} ({_pct(len(rated), len(records))})")
    print(f"  would_take_again_percent == -1 (no data) {'.' * 12} {len(no_wta)}")
    print(f"  total ratings across all professors {'.' * 17} {total_ratings:,}")

    departments = Counter(r.get("department") for r in records if r.get("department"))
    print(f"  distinct departments {'.' * 32} {len(departments)}")

    # RMP genuinely serves some teachers twice within one paginated result set,
    # so this is normally non-zero and is not a bug in the ingester.
    served_dupes = max(0, total_rows - len(records))
    print(f"  duplicate rows served by RMP (deduped away) {'.' * 9} {served_dupes}")

    # ---- examples for anything that failed --------------------------------
    for label, bad in (
        ("null avg_rating", null_avg),
        ("null num_ratings", null_num),
        ("rated but no avg_rating", rated_no_avg),
    ):
        if bad:
            print(f"\n  First {min(5, len(bad))} record(s) with {label}:")
            for rec in bad[:5]:
                print(f"    {json.dumps(rec, ensure_ascii=False)}")

    # ---- samples -----------------------------------------------------------
    if samples > 0 and records:
        print(f"\nSample records ({samples} most-rated per school, plus one unrated)")
        for name in sorted(by_school):
            group = sorted(
                by_school[name], key=lambda r: -(r.get("num_ratings") or 0)
            )
            print(f"\n  --- {name} ---")
            for rec in group[:samples]:
                print(json.dumps(rec, indent=4, ensure_ascii=False))
            tail = next((r for r in reversed(group) if (r.get("num_ratings") or 0) == 0), None)
            if tail:
                print("  (unrated example — sentinels intact:)")
                print(json.dumps(tail, indent=4, ensure_ascii=False))

    # ---- verdict -----------------------------------------------------------
    print()
    print(RULE)
    if problems:
        print(" RESULT: NEEDS ATTENTION")
        for item in problems:
            print(f"   FAIL  {item}")
        for item in warnings:
            print(f"   warn  {item}")
    elif warnings:
        print(" RESULT: PASSED WITH WARNINGS")
        for item in warnings:
            print(f"   warn  {item}")
    else:
        print(" RESULT: PASSED — every professor has a school tag and rating fields.")
    print(RULE)
    print()

    return not problems
