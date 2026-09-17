"""Command line entry point for the simulated-Elo rating engine."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from radiant_ingest.persistence import load_raw_professors

from . import config
from .pipeline import rank, report
from .verify import report as verify_report

log = logging.getLogger("radiant_rank")

EXIT_OK = 0
EXIT_FAILED = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rank_professors",
        description=(
            "Rank ingested professors with a simulated-Elo tournament. Each match "
            "samples from a Beta posterior built from avg_rating and num_ratings, "
            "so confidence has to be earned."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/rank_professors.py                     # rank with defaults\n"
            "  python scripts/rank_professors.py --stability-check   # verify convergence\n"
            "  python scripts/rank_professors.py --min-ratings 10    # stricter threshold\n"
            "  python scripts/rank_professors.py --rounds 400        # longer tournament\n"
        ),
    )
    parser.add_argument(
        "--min-ratings", type=int, default=None, metavar="N",
        help=f"minimum ratings to be ranked (default: {config.MIN_RATINGS})",
    )
    parser.add_argument(
        "--rounds", type=int, default=None, metavar="N",
        help=f"rounds per tournament (default: {config.ELO_ROUNDS})",
    )
    parser.add_argument(
        "--tournaments", type=int, default=None, metavar="N",
        help=(
            "independent tournaments to average "
            f"(default: {config.ELO_TOURNAMENTS}); more cuts seed sensitivity"
        ),
    )
    parser.add_argument(
        "--seed", type=int, default=None, metavar="N",
        help=f"RNG seed; fixed so runs are reproducible (default: {config.RANDOM_SEED})",
    )
    parser.add_argument(
        "--prior-strength", type=float, default=None, metavar="F",
        help=(
            "pseudo-ratings pulling each professor toward the field mean "
            f"(default: {config.PRIOR_STRENGTH}); higher punishes thin evidence harder"
        ),
    )
    parser.add_argument(
        "--stability-check", action="store_true",
        help="re-run with a second seed and report rank correlation (roughly doubles runtime)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help=(
            "check the tournament against the closed-form win probability "
            "(no RNG; an independent check that the simulation is right)"
        ),
    )
    parser.add_argument(
        "--top", type=int, default=25, metavar="N",
        help="how many to print in the overall table (default: 25)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    try:
        records = load_raw_professors()
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return EXIT_FAILED

    try:
        rows, meta = rank(
            records=records,
            min_ratings=args.min_ratings,
            rounds=args.rounds,
            tournaments=args.tournaments,
            seed=args.seed,
            prior_strength=args.prior_strength,
            stability_check=args.stability_check,
            verify=args.verify,
        )
    except ValueError as exc:
        log.error("ranking failed: %s", exc)
        return EXIT_FAILED

    report(rows, meta, top=args.top)
    if meta.get("verification"):
        verify_report(meta["verification"])
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
