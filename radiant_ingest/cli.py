"""Command line entry point for the RateMyProfessors ingestion pass."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence

from . import config
from .client import GraphQLError, IngestError
from .persistence import RawStore, load_raw_professors
from .pipeline import run
from .validate import validate

log = logging.getLogger("radiant_ingest")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_VALIDATION = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest_rmp",
        description=(
            "Pull every professor at the configured schools from RateMyProfessors. "
            "Resumable: re-run after a crash and it picks up from the last checkpoint."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/ingest_rmp.py                 # pull (resumes if interrupted)\n"
            "  python scripts/ingest_rmp.py --fresh         # ignore checkpoint, full re-pull\n"
            "  python scripts/ingest_rmp.py --validate-only # re-check the existing output\n"
            "  python scripts/ingest_rmp.py --school 1232   # UNC only\n"
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="back up existing data + checkpoint and re-pull everything from scratch",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="skip the network entirely; validate the existing professors_raw.json",
    )
    parser.add_argument(
        "--school",
        type=int,
        action="append",
        dest="schools",
        metavar="LEGACY_ID",
        help="restrict to one school by RMP legacy id (repeatable)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="stop each school after N pages (smoke-testing only; leaves the pull incomplete)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        metavar="N",
        help="sample records to print per school (default: 3)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"exit {EXIT_VALIDATION} if validation reports problems",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def _select_schools(legacy_ids: Optional[Sequence[int]]) -> list[dict]:
    if not legacy_ids:
        return list(config.TARGET_SCHOOLS)
    wanted = set(legacy_ids)
    selected = [s for s in config.TARGET_SCHOOLS if int(s["legacy_id"]) in wanted]
    missing = wanted - {int(s["legacy_id"]) for s in selected}
    for legacy_id in sorted(missing):
        # Allow ingesting a school that isn't in TARGET_SCHOOLS yet.
        selected.append(
            {"legacy_id": legacy_id, "expected_name": None, "short_name": f"School-{legacy_id}"}
        )
    return selected


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    if args.validate_only:
        try:
            records = load_raw_professors()
        except (FileNotFoundError, ValueError) as exc:
            log.error("%s", exc)
            return EXIT_FAILED
        ok = validate(records, samples=args.samples)
        return EXIT_OK if ok or not args.strict else EXIT_VALIDATION

    store = RawStore()
    if args.fresh:
        log.info("--fresh: backing up existing artifacts and starting clean")
        store.reset()

    try:
        records, stats = run(
            school_specs=_select_schools(args.schools),
            max_pages=args.max_pages,
            force=args.fresh,
        )
    except GraphQLError as exc:
        log.error("STOPPED on GraphQL error: %s", exc)
        log.error(
            "Progress is checkpointed in %s and raw rows are in %s. "
            "%s was NOT overwritten. Fix the query, then re-run to resume.",
            config.CHECKPOINT_PATH.name,
            config.RAW_JSONL_PATH.name,
            config.RAW_JSON_PATH.name,
        )
        return EXIT_FAILED
    except IngestError as exc:
        log.error("ingestion failed: %s", exc)
        return EXIT_FAILED
    except KeyboardInterrupt:
        log.warning("interrupted — checkpoint saved; re-run to resume where it stopped")
        return EXIT_FAILED

    for stat in stats:
        verb = "skipped (already complete)" if stat.get("skipped") else f"{stat['pages']} page(s)"
        log.info(
            "%s: %d record(s) in %s", stat["school"]["short_name"], stat["fetched"], verb
        )

    ok = validate(records, stats=stats, samples=args.samples)
    log.info("output: %s", config.RAW_JSON_PATH)

    if not ok and args.strict:
        return EXIT_VALIDATION
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
