"""Ingestion pipeline: resolve schools, paginate teachers, persist as we go."""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Optional

from . import config
from .client import GraphQLError, IngestError, RmpClient
from .persistence import Checkpoint, RawStore

log = logging.getLogger(__name__)


# RMP removed the `autocomplete` root field, so school lookup by name no longer
# works. Global IDs are base64 "School-<legacyId>", which we resolve directly
# and then verify with this node() query.
SCHOOL_NODE_QUERY = """
query SchoolNode($id: ID!) {
  node(id: $id) {
    __typename
    ... on School { id legacyId name city state numRatings }
  }
}
"""

# `text: ""` returns every teacher at the school. Kept as a variable so a future
# name-sharding fallback needs no query change.
SEARCH_TEACHERS_QUERY = """
query SearchTeachers($text: String!, $schoolID: ID!, $first: Int!, $after: String) {
  newSearch {
    teachers(query: {text: $text, schoolID: $schoolID}, first: $first, after: $after) {
      resultCount
      edges {
        cursor
        node {
          id
          legacyId
          firstName
          lastName
          department
          avgRating
          avgDifficulty
          numRatings
          wouldTakeAgainPercent
          school { id legacyId name }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


def _clean(value: Any) -> Any:
    """RMP pads some name/department strings with stray whitespace."""
    return value.strip() if isinstance(value, str) else value


def normalize(node: dict[str, Any], school: dict[str, Any]) -> dict[str, Any]:
    """Flatten one teacher node into a raw record, tagged with its school.

    Sentinel values are preserved as the API reported them (see DEVELOPMENT.md):
    an unrated professor has ``num_ratings == 0``, ``avg_rating == 0`` and
    ``would_take_again_percent == -1``. Interpreting those is the rating
    engine's job, not the ingester's.
    """
    node_school = node.get("school") or {}
    return {
        "id": node.get("id"),
        "legacy_id": node.get("legacyId"),
        "first_name": _clean(node.get("firstName")),
        "last_name": _clean(node.get("lastName")),
        "department": _clean(node.get("department")),
        "avg_rating": node.get("avgRating"),
        "avg_difficulty": node.get("avgDifficulty"),
        "num_ratings": node.get("numRatings"),
        "would_take_again_percent": node.get("wouldTakeAgainPercent"),
        # Prefer the per-teacher school object; fall back to the school we
        # queried if that sub-object ever drops out of the schema.
        "school_id": node_school.get("id") or school["gid"],
        "school_legacy_id": node_school.get("legacyId") or school["legacy_id"],
        "school_name": _clean(node_school.get("name")) or school["name"],
    }


def resolve_school(client: RmpClient, spec: dict[str, Any]) -> dict[str, Any]:
    """Turn a configured legacy id into a verified school descriptor."""
    legacy_id = int(spec["legacy_id"])
    gid = config.school_gid(legacy_id)
    data = client.execute(
        SCHOOL_NODE_QUERY, {"id": gid}, op_name=f"school({legacy_id})"
    )
    node = data.get("node")
    if not node or node.get("__typename") != "School":
        raise GraphQLError(
            f"School-{legacy_id} did not resolve to a School node (got {node!r}). "
            "The global-ID scheme may have changed."
        )

    name = _clean(node.get("name")) or str(spec.get("expected_name"))
    expected = spec.get("expected_name")
    if expected and name != expected:
        log.warning(
            "school %d name drift: expected %r, API says %r (continuing)",
            legacy_id, expected, name,
        )

    school = {
        "legacy_id": legacy_id,
        "gid": node.get("id") or gid,
        "name": name,
        "city": _clean(node.get("city")),
        "state": _clean(node.get("state")),
        "short_name": spec.get("short_name") or name,
    }
    log.info(
        "resolved school %d -> %s (%s, %s)",
        legacy_id, school["name"], school["city"], school["state"],
    )
    return school


def fetch_school(
    client: RmpClient,
    school: dict[str, Any],
    checkpoint: Checkpoint,
    store: RawStore,
    *,
    max_pages: Optional[int] = None,
) -> dict[str, Any]:
    """Paginate every teacher at one school, checkpointing after each page."""
    legacy_id = school["legacy_id"]
    entry = checkpoint.school(legacy_id)
    checkpoint.update_school(
        legacy_id,
        gid=school["gid"],
        name=school["name"],
        city=school.get("city"),
        state=school.get("state"),
    )

    cursor: Optional[str] = entry.get("cursor")
    fetched: int = int(entry.get("fetched") or 0)
    result_count: Optional[int] = entry.get("result_count")

    if cursor:
        log.info(
            "%s: resuming from checkpoint at %d record(s)", school["short_name"], fetched
        )

    pages = 0
    exhausted = False

    while True:
        data = client.execute(
            SEARCH_TEACHERS_QUERY,
            {
                "text": "",
                "schoolID": school["gid"],
                "first": config.PAGE_SIZE,
                "after": cursor,
            },
            op_name=f"teachers({school['short_name']}, after={cursor})",
        )

        teachers = (data.get("newSearch") or {}).get("teachers")
        if teachers is None:
            raise GraphQLError(
                f"{school['short_name']}: newSearch.teachers came back null — "
                "likely an invalid schoolID or a schema change."
            )

        result_count = teachers.get("resultCount", result_count)
        edges = teachers.get("edges") or []
        page_info = teachers.get("pageInfo") or {}

        records = [normalize(edge["node"], school) for edge in edges if edge.get("node")]
        store.append_many(records)
        fetched += len(records)
        pages += 1

        cursor = page_info.get("endCursor") or cursor
        has_next = bool(page_info.get("hasNextPage"))

        checkpoint.update_school(
            legacy_id, cursor=cursor, fetched=fetched, result_count=result_count
        )

        log.info(
            "%s: page %d  +%d  (%d/%s)",
            school["short_name"], pages, len(records), fetched,
            result_count if result_count is not None else "?",
        )

        if not has_next or not edges:
            exhausted = True
            break
        if max_pages is not None and pages >= max_pages:
            log.warning(
                "%s: stopping at --max-pages=%d (not complete)",
                school["short_name"], max_pages,
            )
            break

        time.sleep(config.REQUEST_DELAY)

    checkpoint.update_school(legacy_id, done=exhausted)

    return {
        "school": school,
        "pages": pages,
        "fetched": fetched,
        "result_count": result_count,
        "complete": exhausted,
    }


def run(
    *,
    school_specs: Optional[Iterable[dict[str, Any]]] = None,
    max_pages: Optional[int] = None,
    force: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ingest every configured school.

    Returns ``(records, per_school_stats)``. ``professors_raw.json`` is written
    only when every school completed, so that file is never a partial dataset.
    """
    specs = list(school_specs if school_specs is not None else config.TARGET_SCHOOLS)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    checkpoint = Checkpoint.load()
    store = RawStore()
    stats: list[dict[str, Any]] = []

    with RmpClient() as client:
        for index, spec in enumerate(specs):
            legacy_id = int(spec["legacy_id"])

            if checkpoint.is_done(legacy_id) and not force:
                entry = checkpoint.school(legacy_id)
                log.info(
                    "%s: already complete in checkpoint (%d records) — skipping. "
                    "Use --fresh to re-pull.",
                    entry.get("name") or legacy_id, entry.get("fetched") or 0,
                )
                stats.append(
                    {
                        "school": {
                            "legacy_id": legacy_id,
                            "name": entry.get("name"),
                            "short_name": spec.get("short_name") or entry.get("name"),
                            "gid": entry.get("gid"),
                        },
                        "pages": 0,
                        "fetched": entry.get("fetched") or 0,
                        "result_count": entry.get("result_count"),
                        "complete": True,
                        "skipped": True,
                    }
                )
                continue

            if force:
                checkpoint.reset_school(legacy_id)

            school = resolve_school(client, spec)
            stats.append(fetch_school(client, school, checkpoint, store, max_pages=max_pages))

            if index < len(specs) - 1:
                time.sleep(config.INTER_SCHOOL_DELAY)

        log.info("made %d HTTP request(s) this run", client.request_count)

    if not all(s["complete"] for s in stats):
        incomplete = [s["school"]["short_name"] for s in stats if not s["complete"]]
        raise IngestError(
            "not every school completed (" + ", ".join(incomplete) + ") — "
            "professors_raw.json was left untouched. Re-run to resume."
        )

    records = store.finalize()
    return records, stats
