"""Resumability: a crashed pull must not lose or duplicate professors."""

from __future__ import annotations

import json

import pytest

from radiant_ingest.persistence import Checkpoint, RawStore


@pytest.fixture()
def store(tmp_path):
    return RawStore(tmp_path / "raw.jsonl", tmp_path / "raw.json")


def prof(pid, **kw):
    base = {
        "id": pid, "legacy_id": 1, "first_name": "A", "last_name": "B",
        "department": "Dept", "avg_rating": 4.0, "avg_difficulty": 3.0,
        "num_ratings": 10, "would_take_again_percent": 80.0,
        "school_id": "S1", "school_legacy_id": 1, "school_name": "Test U",
    }
    base.update(kw)
    return base


def test_append_then_finalize_round_trips(store):
    store.append_many([prof("A"), prof("B")])
    out = store.finalize()
    assert {r["id"] for r in out} == {"A", "B"}
    assert json.loads(store.json_path.read_text()) == out


def test_a_replayed_page_dedupes_instead_of_duplicating(store):
    """A crash between writing the log and saving the checkpoint replays one
    page on resume. That must collapse, not double up."""
    store.append_many([prof("A"), prof("B")])
    store.append_many([prof("B"), prof("C")])  # B re-fetched

    out = store.finalize()
    assert sorted(r["id"] for r in out) == ["A", "B", "C"]


def test_the_later_copy_of_a_record_wins(store):
    store.append_many([prof("A", num_ratings=10)])
    store.append_many([prof("A", num_ratings=99)])
    out = store.finalize()
    assert len(out) == 1
    assert out[0]["num_ratings"] == 99


def test_a_torn_final_line_is_skipped_not_fatal(store):
    """A hard kill mid-write leaves a partial last line in the log."""
    store.append_many([prof("A")])
    with store.jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "B", "num_rat')

    out = store.finalize()
    assert [r["id"] for r in out] == ["A"]


def test_records_without_an_id_are_dropped(store):
    store.append_many([prof("A"), {"first_name": "orphan"}])
    assert [r["id"] for r in store.finalize()] == ["A"]


def test_checkpoint_persists_and_reloads(tmp_path):
    path = tmp_path / "ck.json"
    ck = Checkpoint.load(path)
    ck.update_school(1232, cursor="abc", fetched=200, result_count=4915)

    again = Checkpoint.load(path)
    entry = again.school(1232)
    assert entry["cursor"] == "abc"
    assert entry["fetched"] == 200
    assert not again.is_done(1232)

    again.update_school(1232, done=True)
    assert Checkpoint.load(path).is_done(1232)


def test_an_unreadable_checkpoint_starts_over_rather_than_crashing(tmp_path):
    path = tmp_path / "ck.json"
    path.write_text("{not json at all")
    ck = Checkpoint.load(path)
    assert ck.state["schools"] == {}


def test_a_checkpoint_from_a_future_version_is_discarded(tmp_path):
    path = tmp_path / "ck.json"
    path.write_text(json.dumps({"version": 99, "schools": {"1232": {"done": True}}}))
    ck = Checkpoint.load(path)
    # Trusting an unknown layout would silently skip a school that never ran.
    assert not ck.is_done(1232)


def test_reset_backs_up_rather_than_deleting(store):
    store.append_many([prof("A")])
    store.finalize()
    store.reset()

    assert not store.jsonl_path.exists()
    backups = list(store.jsonl_path.parent.glob("raw.jsonl.bak.*"))
    assert len(backups) == 1
