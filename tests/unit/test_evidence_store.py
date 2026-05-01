"""Tests for src/evidence_store.py InMemoryEvidenceStore."""

from src.evidence_store import InMemoryEvidenceStore


def test_put_and_get_latest():
    s = InMemoryEvidenceStore()
    s.put("ups-A", "ups_battery_transfer",
          {"completed_at": "2026-01-01T00:00:00", "passed": True})
    s.put("ups-A", "ups_battery_transfer",
          {"completed_at": "2026-02-01T00:00:00", "passed": True})
    latest = s.get_latest("ups-A", "ups_battery_transfer")
    assert latest["completed_at"] == "2026-02-01T00:00:00"


def test_get_latest_returns_none_when_empty():
    s = InMemoryEvidenceStore()
    assert s.get_latest("nope", "nope") is None


def test_list_runs_in_insertion_order():
    s = InMemoryEvidenceStore()
    for d in (1, 2, 3):
        s.put("d", "t", {"completed_at": f"2026-0{d}-01", "passed": True})
    runs = s.list_runs("d", "t")
    assert [r["completed_at"] for r in runs] == [
        "2026-01-01", "2026-02-01", "2026-03-01"]


def test_list_runs_returns_empty_for_unknown_key():
    s = InMemoryEvidenceStore()
    s.put("d", "t", {"completed_at": "x"})
    assert s.list_runs("d", "other_test") == []
    assert s.list_runs("other_dev", "t") == []


def test_list_for_device_groups_test_names():
    s = InMemoryEvidenceStore()
    s.put("xfmr-A1", "ttr", {"completed_at": "2026-01-15T00:00:00"})
    s.put("xfmr-A1", "pi",  {"completed_at": "2026-01-10T00:00:00"})
    s.put("xfmr-A1", "ttr", {"completed_at": "2026-02-01T00:00:00"})
    s.put("ups-A", "ups_battery_transfer", {"completed_at": "2026-02-15T00:00:00"})
    runs = s.list_for_device("xfmr-A1")
    assert len(runs) == 3
    # Ordered by completed_at
    assert runs[0]["completed_at"] == "2026-01-10T00:00:00"
    assert runs[-1]["completed_at"] == "2026-02-01T00:00:00"


def test_put_does_not_share_dict_reference():
    s = InMemoryEvidenceStore()
    rec = {"completed_at": "2026-01-01", "passed": True}
    s.put("d", "t", rec)
    rec["passed"] = False  # mutate original
    stored = s.get_latest("d", "t")
    assert stored["passed"] is True  # store kept its own copy
