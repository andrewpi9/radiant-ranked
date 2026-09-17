"""Rank the ingested professors and write the leaderboard."""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Sequence

from radiant_ingest.persistence import load_raw_professors

from . import config, tiers
from . import verify as verify_mod
from .elo import simulate_ensemble, spearman
from .model import build_contenders, posterior_mean, to_rating

log = logging.getLogger(__name__)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _assign_subranks(rows: list[dict[str, Any]], key: str, prefix: str) -> None:
    """Rank within each group (department / school), preserving global order."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    for members in groups.values():
        size = len(members)
        for position, row in enumerate(members, start=1):
            row[f"{prefix}_rank"] = position
            row[f"{prefix}_size"] = size


def rank(
    *,
    records: Optional[Sequence[dict[str, Any]]] = None,
    min_ratings: Optional[int] = None,
    rounds: Optional[int] = None,
    tournaments: Optional[int] = None,
    seed: Optional[int] = None,
    prior_strength: Optional[float] = None,
    stability_check: bool = False,
    verify: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full ranking pass. Returns ``(ranked_rows, meta)``."""
    if records is None:
        records = load_raw_professors()
    log.info("loaded %d professor record(s)", len(records))

    min_ratings = config.MIN_RATINGS if min_ratings is None else min_ratings
    contenders, prior_mean = build_contenders(
        records, min_ratings=min_ratings, prior_strength=prior_strength
    )
    excluded = len(records) - len(contenders)
    log.info(
        "ranking %d professor(s) with >=%d ratings (%d excluded); "
        "prior mean %.4f (%.2f/5) at strength %.1f",
        len(contenders), min_ratings, excluded, prior_mean,
        to_rating(prior_mean), config.PRIOR_STRENGTH if prior_strength is None else prior_strength,
    )
    if len(contenders) < 2:
        raise ValueError(
            f"only {len(contenders)} professor(s) met the >={min_ratings} rating "
            "threshold — nothing to rank"
        )

    simulate_ensemble(contenders, tournaments=tournaments, rounds=rounds, seed=seed)

    ordered = sorted(contenders, key=lambda c: -c.elo)
    total = len(ordered)
    tier_of = tiers.assign(total)
    rows: list[dict[str, Any]] = []
    for position, c in enumerate(ordered, start=1):
        tier = tiers.TIERS[tier_of[position - 1]]
        rows.append(
            {
                "rank": position,
                "tier": tier.name,
                "tier_slug": tier.slug,
                "elo": round(c.elo, 1),
                # Spread across the ensemble: how firm this rank actually is.
                "elo_stddev": round(c.elo_stddev, 1),
                "percentile": round(100.0 * (total - position) / (total - 1), 2),
                "id": c.id,
                "name": c.name,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "department": c.department,
                "school_name": c.school_name,
                "school_id": c.school_id,
                "avg_rating": c.avg_rating,
                # Posterior mean back on the 1-5 scale: what the evidence
                # actually supports, after shrinkage.
                "shrunk_rating": round(to_rating(posterior_mean(c)), 3),
                "avg_difficulty": c.avg_difficulty,
                "num_ratings": c.num_ratings,
                "would_take_again_percent": c.would_take_again_percent,
            }
        )

    _assign_subranks(rows, "department", "department")
    _assign_subranks(rows, "school_name", "school")

    # Naive rank by raw average, for the "what did the model change" report.
    naive_order = sorted(
        rows, key=lambda r: (-r["avg_rating"], -r["num_ratings"], r["name"])
    )
    naive_rank = {r["id"]: i for i, r in enumerate(naive_order, start=1)}
    for row in rows:
        row["naive_rank"] = naive_rank[row["id"]]
        row["rank_delta"] = naive_rank[row["id"]] - row["rank"]

    meta: dict[str, Any] = {
        "total_records": len(records),
        "ranked": total,
        "excluded": excluded,
        "min_ratings": min_ratings,
        "prior_mean_unit": round(prior_mean, 6),
        "prior_mean_rating": round(to_rating(prior_mean), 3),
        "prior_strength": config.PRIOR_STRENGTH if prior_strength is None else prior_strength,
        "rounds": config.ELO_ROUNDS if rounds is None else rounds,
        "tournaments": config.ELO_TOURNAMENTS if tournaments is None else tournaments,
        "seed": config.RANDOM_SEED if seed is None else seed,
        "tiers": tiers.summarize(tier_of),
    }

    if stability_check:
        # Re-run with a completely disjoint block of seeds, so the comparison
        # shares no randomness with the published ranking at all.
        n_tourn = meta["tournaments"]
        alt_seed = meta["seed"] + n_tourn
        log.info("stability check: second ensemble from seed %d", alt_seed)
        alt = [replace(c, elo=config.ELO_START, elo_stddev=0.0) for c in contenders]
        simulate_ensemble(alt, tournaments=n_tourn, rounds=rounds, seed=alt_seed)
        alt_ranks = {
            c.id: i for i, c in enumerate(sorted(alt, key=lambda c: -c.elo), start=1)
        }
        base_ranks = {r["id"]: r["rank"] for r in rows}
        rho = spearman(base_ranks, alt_ranks)

        def overlap(k: int) -> int:
            return len(
                {r["id"] for r in rows[:k]} & {i for i, rk in alt_ranks.items() if rk <= k}
            )

        meta["stability"] = {
            "alt_seed": alt_seed,
            "spearman": round(rho, 5),
            "top10_overlap": overlap(10),
            "top50_overlap": overlap(50),
            "top100_overlap": overlap(100),
        }
        log.info(
            "stability: spearman rho=%.5f, top-10 %d/10, top-50 %d/50, top-100 %d/100",
            rho, overlap(10), overlap(50), overlap(100),
        )

    if verify:
        log.info("verifying the tournament against the closed-form win probability")
        meta["verification"] = verify_mod.compare_to_elo(contenders)
        log.info(
            "verification: spearman rho=%.5f vs direct computation",
            meta["verification"]["spearman"],
        )

    payload = {"meta": meta, "professors": rows}
    _write_json(config.RANKINGS_PATH, payload)
    log.info("wrote %d ranked professor(s) to %s", total, config.RANKINGS_PATH)
    return rows, meta


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

RULE = "=" * 78


def _fmt(row: dict[str, Any], *, rank_key: str = "rank") -> str:
    wta = row.get("would_take_again_percent")
    wta_txt = f"{wta:.0f}%" if isinstance(wta, (int, float)) and wta >= 0 else "  - "
    label = config.school_label(row.get("school_id", ""), row.get("school_name", ""))
    return (
        f"  {row[rank_key]:>5}  {row.get('tier', ''):<10} "
        f"{row['elo']:>7.1f} ±{row.get('elo_stddev', 0.0):>5.1f}  "
        f"{row['name'][:24]:<24} "
        f"{row['department'][:18]:<18} {row['avg_rating']:>4.1f} "
        f"{row['num_ratings']:>5}  {wta_txt:>5}  {label}"
    )


def _header(rank_label: str = "rank") -> str:
    return (
        f"  {rank_label:>5}  {'tier':<10} {'elo':>7} {'±sd':>6}  {'professor':<24} "
        f"{'department':<18} {'avg':>4} {'n':>5}  {'again':>5}  {'sch'}"
    )


def report(rows: Sequence[dict[str, Any]], meta: dict[str, Any], *, top: int = 25) -> None:
    print()
    print(RULE)
    print(" RADIANT RANKED — SIMULATED-ELO LEADERBOARD")
    print(RULE)
    print(
        f"\n  {meta['ranked']:,} professors ranked "
        f"({meta['excluded']:,} excluded: fewer than {meta['min_ratings']} ratings)"
    )
    print(
        f"  {meta['tournaments']} tournaments x {meta['rounds']} rounds, "
        f"seed {meta['seed']}, prior {meta['prior_strength']:.0f} pseudo-ratings at "
        f"{meta['prior_mean_rating']:.2f}/5"
    )
    print("  '±sd' is the Elo spread across tournaments — a large value means a near-tie.")
    if "stability" in meta:
        st = meta["stability"]
        print(
            f"\n  stability vs a disjoint ensemble (seed {st['alt_seed']}): "
            f"Spearman {st['spearman']:.5f}\n"
            f"    top-10 {st['top10_overlap']}/10, "
            f"top-50 {st['top50_overlap']}/50, "
            f"top-100 {st['top100_overlap']}/100"
        )

    if meta.get("tiers"):
        print("\n\nTIER DISTRIBUTION\n")
        print(f"  {'tier':<11}{'profs':>7}{'share':>8}{'target':>8}  {'ranks':<15}")
        print(f"  {'-' * 58}")
        for t in meta["tiers"]:
            span = f"{t['first_rank']}–{t['last_rank']}" if t["count"] else "—"
            print(
                f"  {t['name']:<11}{t['count']:>7}{t['actual_share']:>7.1f}%"
                f"{t['target_share']:>7.1f}%  {span:<15}"
            )

    print(f"\n\nTOP {top} OVERALL\n")
    print(_header())
    for row in rows[:top]:
        print(_fmt(row))

    # Per school
    by_school: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_school[row["school_name"]].append(row)
    for school in sorted(by_school):
        members = by_school[school]
        print(f"\n\nTOP 10 — {school} ({len(members):,} ranked)\n")
        print(_header("s.rank"))
        for row in members[:10]:
            print(_fmt(row, rank_key="school_rank"))

    # Biggest departments
    sizes = Counter(row["department"] for row in rows)
    big = [d for d, n in sizes.most_common() if n >= config.MIN_DEPARTMENT_SIZE][:6]
    print("\n\nDEPARTMENT LEADERS (6 largest departments)\n")
    for dept in big:
        members = [r for r in rows if r["department"] == dept]
        leader = members[0]
        print(
            f"  {dept[:24]:<24} ({sizes[dept]:>3} profs)  #1 {leader['name'][:24]:<24} "
            f"elo {leader['elo']:>7.1f}  avg {leader['avg_rating']:.1f} "
            f"({leader['num_ratings']} ratings)"
        )

    # What the model actually changed relative to a naive average sort.
    print("\n\nWHAT THE MODEL CHANGED (vs. sorting by raw avg_rating)\n")
    demoted = sorted(rows, key=lambda r: r["rank_delta"])[:5]
    promoted = sorted(rows, key=lambda r: -r["rank_delta"])[:5]
    print("  Biggest demotions — high average, thin evidence:")
    for row in demoted:
        print(
            f"    avg {row['avg_rating']:.1f} over {row['num_ratings']:>4} ratings  "
            f"naive #{row['naive_rank']:<5} -> elo #{row['rank']:<5} "
            f"({row['rank_delta']:+d})  {row['name'][:24]}"
        )
    print("\n  Biggest promotions — slightly lower average, far more evidence:")
    for row in promoted:
        print(
            f"    avg {row['avg_rating']:.1f} over {row['num_ratings']:>4} ratings  "
            f"naive #{row['naive_rank']:<5} -> elo #{row['rank']:<5} "
            f"({row['rank_delta']:+d})  {row['name'][:24]}"
        )
    print()
    print(RULE)
    print(f" wrote {config.RANKINGS_PATH}")
    print(RULE)
    print()
