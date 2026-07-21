"""Unit tests for the Forge/Matcher->Ledger wiring (docs/ECONOMY.md build
stage 3 integration, the mirror of intake/test_intake.py's Salvage-side
ledger tests).
Run with: python3 -m pytest ledger/test_record_draws.py -v
"""

import json
import os

import pytest

from ledger import Ledger
import record_draws
from record_draws import CREDIT_PER_BUILD_V0, record_draws as run_record_draws


def _write_draws_json(path, drawer, builds):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": "2026-07-20T00:00:00+00:00",
                   "drawer": drawer, "builds": builds}, f)


def test_builder_draw_records_and_debits_balance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = Ledger()
    ledger.grant_credit("alice", 10)

    _write_draws_json("matcher/draws.json",
                       {"id": "alice", "role": "builder", "job_id": None, "client": None},
                       [{"template": "Cold frame (46.0x32.0 window lid)",
                         "consumed_ids": ["S08#1", "S04#3"]}])

    recorded, failed = run_record_draws(ledger)

    assert (recorded, failed) == (1, 0)
    assert ledger.balance("alice") == 10 - CREDIT_PER_BUILD_V0
    draws = [r for r in ledger.all_records() if r["type"] == "draw"]
    assert len(draws) == 1
    assert draws[0]["data"]["drawer"] == "alice"
    assert draws[0]["data"]["role"] == "builder"
    assert draws[0]["data"]["consumed_ids"] == ["S08#1", "S04#3"]
    assert not os.path.exists("matcher/draws.json")   # artifact cleared after processing


def test_contractor_draw_requires_and_records_job_and_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = Ledger()
    ledger.grant_credit("carla", 10)

    _write_draws_json("matcher/draws.json",
                       {"id": "carla", "role": "contractor",
                        "job_id": "job-42", "client": "Ms. Nguyen"},
                       [{"template": "Cold frame (46.0x32.0 window lid)",
                         "consumed_ids": ["S08#1"]}])

    recorded, failed = run_record_draws(ledger)

    assert (recorded, failed) == (1, 0)
    draws = [r for r in ledger.all_records() if r["type"] == "draw"]
    assert draws[0]["data"]["job_id"] == "job-42"
    assert draws[0]["data"]["client"] == "Ms. Nguyen"
    # Same debit mechanism as a builder draw -- job/client is attribution
    # metadata, not a different (or absent) payment path. docs/ECONOMY.md's
    # "labor stays off the credit economy" is about the contractor's TIME,
    # not the materials they draw.
    assert ledger.balance("carla") == 10 - CREDIT_PER_BUILD_V0


def test_no_draws_json_is_a_clean_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ledger = Ledger()

    recorded, failed = run_record_draws(ledger)

    assert (recorded, failed) == (0, 0)
    out = capsys.readouterr().out
    assert "no draws.json found" in out


def test_simulated_ledger_failure_is_warned_and_does_not_stop_the_run(tmp_path, monkeypatch, capsys):
    # Two builds; the ledger write fails for both (simulated), but each is
    # attempted independently and the artifact is still cleared afterward --
    # the "physical build already happened" guarantee lives entirely in
    # matcher.jl (cutsheets.txt/draws.json are already on disk before this
    # script runs), so a total ledger failure here loses no build work.
    monkeypatch.chdir(tmp_path)
    ledger = Ledger()
    ledger.grant_credit("dana", 10)

    _write_draws_json("matcher/draws.json",
                       {"id": "dana", "role": "builder", "job_id": None, "client": None},
                       [{"template": "Cold frame A", "consumed_ids": ["S01#1"]},
                        {"template": "Cold frame B", "consumed_ids": ["S02#1"]}])

    def boom(self, *a, **kw):
        raise RuntimeError("simulated write failure")
    monkeypatch.setattr(Ledger, "draw", boom)

    recorded, failed = run_record_draws(ledger)   # must not raise

    out = capsys.readouterr().out
    assert (recorded, failed) == (0, 2)   # both builds attempted independently, both failed
    assert out.count("LEDGER  WARNING") == 2
    assert "simulated write failure" in out
    assert not os.path.exists("matcher/draws.json")   # cleared even on total failure


def test_insufficient_credit_is_advisory_not_fatal(tmp_path, monkeypatch, capsys):
    # No grant_credit at all -- draw() raises LedgerError from its own
    # balance check. That's just another per-build failure this script
    # catches and warns about; it isn't special-cased, and it doesn't stop
    # the run or block the (already-physical, already-on-disk) build.
    monkeypatch.chdir(tmp_path)
    ledger = Ledger()

    _write_draws_json("matcher/draws.json",
                       {"id": "erin", "role": "builder", "job_id": None, "client": None},
                       [{"template": "Bike cargo trailer", "consumed_ids": ["S03#1"]}])

    recorded, failed = run_record_draws(ledger)

    assert (recorded, failed) == (0, 1)
    out = capsys.readouterr().out
    assert "LEDGER  WARNING" in out
    assert ledger.balance("erin") == 0
    assert not os.path.exists("matcher/draws.json")
