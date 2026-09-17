"""Durable progress: the append log, the final JSON, and the checkpoint.

Write ordering per page is deliberate:

  1. append the page's records to the JSONL log (flushed + fsynced)
  2. only then advance the checkpoint cursor

A crash between the two re-fetches at most one page on resume, which shows up
as duplicate ids and is removed by :meth:`RawStore.finalize` deduping on ``id``.
The reverse ordering would silently lose a page.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config

log = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON via temp file + rename so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _backup(path: Path, stamp: str) -> None:
    if path.exists():
        dest = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.move(str(path), str(dest))
        log.info("backed up %s -> %s", path.name, dest.name)


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------


class Checkpoint:
    """Per-school ingestion progress, persisted after every page.

    Schema::

        {"version": 1, "started_at": ..., "updated_at": ...,
         "schools": {"<legacy_id>": {"gid", "name", "city", "state",
                                     "result_count", "fetched", "cursor",
                                     "done", "started_at", "updated_at"}}}
    """

    def __init__(self, path: Path, state: dict[str, Any]) -> None:
        self.path = path
        self.state = state

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Checkpoint":
        path = path or config.CHECKPOINT_PATH
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                log.warning("checkpoint at %s is unreadable (%s) — starting fresh", path, exc)
                state = {}
            else:
                if state.get("version") != CHECKPOINT_VERSION:
                    log.warning(
                        "checkpoint version %r != %r — starting fresh",
                        state.get("version"), CHECKPOINT_VERSION,
                    )
                    state = {}
        else:
            state = {}

        if not state:
            state = {
                "version": CHECKPOINT_VERSION,
                "started_at": utcnow_iso(),
                "updated_at": utcnow_iso(),
                "schools": {},
            }
        state.setdefault("schools", {})
        return cls(path, state)

    def school(self, legacy_id: int) -> dict[str, Any]:
        return self.state["schools"].setdefault(
            str(legacy_id),
            {
                "legacy_id": legacy_id,
                "gid": None,
                "name": None,
                "result_count": None,
                "fetched": 0,
                "cursor": None,
                "done": False,
                "started_at": utcnow_iso(),
                "updated_at": None,
            },
        )

    def update_school(self, legacy_id: int, **fields: Any) -> None:
        entry = self.school(legacy_id)
        entry.update(fields)
        entry["updated_at"] = utcnow_iso()
        self.save()

    def save(self) -> None:
        self.state["updated_at"] = utcnow_iso()
        _atomic_write_json(self.path, self.state)

    def is_done(self, legacy_id: int) -> bool:
        return bool(self.school(legacy_id).get("done"))

    def reset_school(self, legacy_id: int) -> None:
        self.state["schools"].pop(str(legacy_id), None)
        self.save()


# --------------------------------------------------------------------------
# Raw record store
# --------------------------------------------------------------------------


class RawStore:
    """Append-only JSONL log plus the deduped ``professors_raw.json`` output."""

    def __init__(
        self,
        jsonl_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
    ) -> None:
        self.jsonl_path = jsonl_path or config.RAW_JSONL_PATH
        self.json_path = json_path or config.RAW_JSON_PATH
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def append_many(self, records: Iterable[dict[str, Any]]) -> int:
        """Append records to the log, fsynced before returning."""
        records = list(records)
        if not records:
            return 0
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return len(records)

    def read_log(self) -> list[dict[str, Any]]:
        """Read every record from the append log, skipping any torn last line."""
        if not self.jsonl_path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.jsonl_path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    # Only plausible on the final line after a hard kill.
                    log.warning("skipping malformed JSONL line %d in %s", lineno, self.jsonl_path.name)
        return records

    def finalize(self) -> list[dict[str, Any]]:
        """Dedupe the log by professor id and write the final JSON output.

        Later records win, so a re-fetched page refreshes rather than duplicates.
        """
        records = self.read_log()
        deduped: dict[str, dict[str, Any]] = {}
        for rec in records:
            key = rec.get("id")
            if key is None:
                continue
            deduped[key] = rec

        ordered = sorted(
            deduped.values(),
            key=lambda r: (
                r.get("school_name") or "",
                -(r.get("num_ratings") or 0),
                r.get("last_name") or "",
                r.get("first_name") or "",
            ),
        )
        _atomic_write_json(self.json_path, ordered)
        dropped = len(records) - len(ordered)
        log.info(
            "wrote %d professors to %s%s",
            len(ordered),
            self.json_path,
            f" ({dropped} duplicate rows collapsed)" if dropped else "",
        )
        return ordered

    def reset(self) -> None:
        """Back up existing artifacts and start clean (``--fresh``)."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for path in (self.jsonl_path, self.json_path, config.CHECKPOINT_PATH):
            _backup(path, stamp)


def load_raw_professors(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Read ``professors_raw.json``. Used by ``--validate-only`` and later phases."""
    path = path or config.RAW_JSON_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist — run `python scripts/ingest_rmp.py` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
