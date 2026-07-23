"""Unit tests for Ledger v0 (docs/ECONOMY.md build stage 3).
Run with: python3 -m pytest ledger/test_ledger.py -v
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from ledger import COMMONS_ID, CREDIT_PER_LAPSE_V0, Ledger, LedgerError


class FakeClock:
    """Controllable clock for deterministic earmark-expiry tests -- avoids
    both real sleeps (slow) and comparing timestamps that are only
    microseconds apart in a fast test (which can't distinguish "activity
    reset the deadline" from "didn't," since both would be ~now either way)."""
    def __init__(self, start=None):
        self.now = start or datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def ledger(tmp_path, clock):
    return Ledger(path=str(tmp_path / "ledger.jsonl"), inactivity_window=timedelta(days=1),
                  clock=clock)


def test_deposit_banks_credit(ledger):
    ledger.deposit("alice", "K001", credit_amount=5, category="sheet", family="corrugated")
    assert ledger.balance("alice") == 5


def test_draw_debits_balance_correctly(ledger):
    ledger.deposit("alice", "K001", credit_amount=10)
    ledger.draw("alice", "builder", "K002", credit_amount=4)
    assert ledger.balance("alice") == 6
    ledger.draw("alice", "builder", "K003", credit_amount=6)
    assert ledger.balance("alice") == 0


def test_draw_exceeding_balance_raises(ledger):
    ledger.deposit("alice", "K001", credit_amount=3)
    with pytest.raises(LedgerError):
        ledger.draw("alice", "builder", "K002", credit_amount=4)


def test_earmark_holds_while_active_and_releases_when_stalled(ledger, clock):
    project = ledger.declare_project("bob", "cold frame")
    mark = ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(hours=1))
    assert ledger.earmark_status(mark["id"]) == "active"
    assert ledger.is_earmarked("K010") is True

    # Past the 1-day inactivity window with no further activity -> expired.
    clock.advance(timedelta(days=2))
    assert ledger.earmark_status(mark["id"]) == "expired"
    assert ledger.is_earmarked("K010") is False


def test_project_activity_resets_the_earmark_clock(ledger, clock):
    # Any record carrying project_id counts as activity -- a status update
    # well before the window lapses should push the deadline out, proving
    # the generic (not deposit/draw-specific) activity mechanism works.
    project = ledger.declare_project("bob", "cold frame")
    mark = ledger.earmark(project["id"], "K010", "erin")
    t0 = ledger.project_last_activity(project["id"])

    clock.advance(timedelta(hours=23))          # still within the 1-day window from t0
    ledger.update_project_status(project["id"], "in_progress", note="cutting rails")
    t1 = ledger.project_last_activity(project["id"])
    assert t1 > t0

    clock.advance(timedelta(hours=2))           # now 25h past t0 (would be expired from t0 alone)
    assert ledger.earmark_status(mark["id"]) == "active"    # but only 2h past t1 -> still active

    clock.advance(timedelta(days=2))
    assert ledger.earmark_status(mark["id"]) == "expired"   # now stalled from t1 too


def test_earmark_without_project_raises(ledger):
    with pytest.raises(LedgerError):
        ledger.earmark("no-such-project", "K010", "erin")


def test_contractor_draw_requires_job_and_client(ledger):
    ledger.grant_credit("carla", 10)
    with pytest.raises(LedgerError):
        ledger.draw("carla", "contractor", "K001", credit_amount=5)


def test_contractor_draw_attributed_separately_from_builder_draw(ledger):
    ledger.grant_credit("dana", 20)     # DIY builder
    ledger.grant_credit("carla", 20)    # contractor

    builder_draw = ledger.draw("dana", "builder", "K001", credit_amount=5)
    contractor_draw = ledger.draw("carla", "contractor", "K002", credit_amount=5,
                                   job_id="job-42", client="Ms. Nguyen")

    assert builder_draw["data"]["job_id"] is None
    assert builder_draw["data"]["client"] is None
    assert contractor_draw["data"]["job_id"] == "job-42"
    assert contractor_draw["data"]["client"] == "Ms. Nguyen"

    work = ledger.record_certified_work(
        "carla", job_id="job-42", client="Ms. Nguyen",
        description="Built cold frame per plan", draw_ids=[contractor_draw["id"]])
    assert work["type"] == "certified_work"

    # Certified-work records exist only for the contractor's job, never
    # generated for the DIY builder's draw.
    all_certified = [r for r in ledger.all_records() if r["type"] == "certified_work"]
    assert len(all_certified) == 1
    assert all_certified[0]["data"]["contractor"] == "carla"


def test_hash_chain_verifies_and_detects_tampering(ledger, tmp_path):
    ledger.deposit("alice", "K001", credit_amount=5)
    ledger.grant_credit("alice", 2, note="cash buy-in")
    ledger.draw("alice", "builder", "K002", credit_amount=3)
    assert ledger.verify_chain() is True

    # Tamper with history directly on disk -- change a credited amount --
    # and confirm the chain now reports broken, proving the log is
    # tamper-EVIDENT even though nothing stops direct file edits.
    path = tmp_path / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["data"]["credit_amount"] = 999
    lines[0] = json.dumps(tampered, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    tampered_ledger = Ledger(path=str(path))
    with pytest.raises(LedgerError):
        tampered_ledger.verify_chain()


def test_deposit_feeding_project_does_not_earmark_by_default(ledger):
    # Attribution and reservation are decoupled: feeding a project banks no
    # credit and counts as activity, but does NOT by itself lock the
    # inventory to that project -- earmark=True is a separate, deliberate
    # claim (see the Ledger.deposit docstring).
    project = ledger.declare_project("erin", "pallet shed")
    ledger.deposit("erin", "K099", project_id=project["id"])
    assert ledger.balance("erin") == 0
    assert ledger.is_earmarked("K099") is False


def test_deposit_feeding_project_with_earmark_true_reserves_it(ledger):
    project = ledger.declare_project("erin", "pallet shed")
    ledger.deposit("erin", "K099", project_id=project["id"], earmark=True)
    assert ledger.balance("erin") == 0
    assert ledger.is_earmarked("K099") is True


def test_deposit_feeding_project_without_earmark_still_counts_as_activity(ledger, clock):
    # The deposit itself carries project_id even without earmark=True, so
    # it still resets the clock for the project's OTHER, already-earmarked
    # inventory.
    project = ledger.declare_project("erin", "pallet shed")
    mark = ledger.earmark(project["id"], "K001", "erin")
    clock.advance(timedelta(hours=23))
    ledger.deposit("erin", "K099", project_id=project["id"])   # no earmark=True
    clock.advance(timedelta(hours=2))   # 25h past the original earmark, but only 2h past this deposit
    assert ledger.earmark_status(mark["id"]) == "active"


def test_deposit_with_nonexistent_project_raises_even_without_earmark(ledger):
    # Regression guard: project existence used to be validated as a free
    # side effect of deposit() always calling earmark() internally. Now
    # that earmark is opt-in, a bogus project_id must still be caught by
    # deposit() itself -- not silently accepted just because earmark=False
    # skips the check that earmark() would have done.
    with pytest.raises(LedgerError):
        ledger.deposit("erin", "K099", project_id="no-such-project")


def test_commons_can_receive_credit(ledger):
    ledger.deposit(COMMONS_ID, "K200", credit_amount=1)
    assert ledger.balance(COMMONS_ID) == 1


def test_commons_cannot_own_a_project(ledger):
    with pytest.raises(LedgerError):
        ledger.declare_project(COMMONS_ID, "not a real project")


def test_commons_cannot_draw(ledger):
    ledger.grant_credit(COMMONS_ID, 10)
    with pytest.raises(LedgerError):
        ledger.draw(COMMONS_ID, "builder", "K001", credit_amount=5)


# ---------------------------------------------------------------------------
# Earmark lapse -> credit (docs/ECONOMY.md: good-faith contribution must
# never evaporate). PR foundation for peak-value crediting (stage 2) and
# reinstatement/recovery (stage 3) -- deliberately flat-credit only here.
# ---------------------------------------------------------------------------

def test_lapsed_earmark_releases_material_and_credits_contributor(ledger, clock):
    project = ledger.declare_project("bob", "cold frame")
    mark = ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(days=2))   # past the 1-day inactivity window
    lapsed = ledger.expire_stale_earmarks()

    assert len(lapsed) == 1
    assert lapsed[0]["type"] == "earmark_lapse"
    assert lapsed[0]["data"]["earmark_id"] == mark["id"]
    assert lapsed[0]["data"]["contributor"] == "erin"
    assert ledger.balance("erin") == CREDIT_PER_LAPSE_V0
    # The credit flows to the earmarking contributor, not the project owner.
    assert ledger.balance("bob") == 0


def test_lapsed_earmark_material_returns_to_fungible_reservoir(ledger, clock):
    project = ledger.declare_project("bob", "cold frame")
    ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(days=2))
    ledger.expire_stale_earmarks()

    assert ledger.is_earmarked("K010") is False


def test_expire_stale_earmarks_twice_does_not_double_credit(ledger, clock):
    project = ledger.declare_project("bob", "cold frame")
    ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(days=2))
    first_pass = ledger.expire_stale_earmarks()
    second_pass = ledger.expire_stale_earmarks()

    assert len(first_pass) == 1
    assert len(second_pass) == 0   # already materialized -- nothing new to lapse
    assert ledger.balance("erin") == CREDIT_PER_LAPSE_V0   # not double-credited
    lapse_records = [r for r in ledger.all_records() if r["type"] == "earmark_lapse"]
    assert len(lapse_records) == 1


def test_active_earmark_is_untouched_by_expire(ledger, clock):
    project = ledger.declare_project("bob", "cold frame")
    mark = ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(hours=1))   # well within the 1-day window
    lapsed = ledger.expire_stale_earmarks()

    assert lapsed == []
    assert ledger.earmark_status(mark["id"]) == "active"
    assert ledger.is_earmarked("K010") is True
    assert ledger.balance("erin") == 0


def test_lapse_credit_goes_to_the_contributor_not_the_project_owner(ledger, clock):
    # A donor can earmark material against someone ELSE's declared project
    # (deposit/earmark carries no owner==contributor requirement today) --
    # the lapse credit must follow the contributor who made the claim, not
    # whoever owns the project it was claimed against.
    project = ledger.declare_project("bob", "cold frame")
    ledger.earmark(project["id"], "K010", "alice")

    clock.advance(timedelta(days=2))
    ledger.expire_stale_earmarks()

    assert ledger.balance("alice") == CREDIT_PER_LAPSE_V0
    assert ledger.balance("bob") == 0


def test_earmark_status_stays_expired_after_lapse_even_if_project_reactivates(ledger, clock):
    # Once materialized, a lapse is permanent -- later project activity
    # must not resurrect an earmark whose material and credit have already
    # moved. This is the coexistence fix: earmark_lapse records don't carry
    # a top-level project_id, so they can't themselves count as activity.
    project = ledger.declare_project("bob", "cold frame")
    mark = ledger.earmark(project["id"], "K010", "erin")

    clock.advance(timedelta(days=2))
    ledger.expire_stale_earmarks()
    assert ledger.earmark_status(mark["id"]) == "expired"

    ledger.update_project_status(project["id"], "in_progress", note="picking back up")
    assert ledger.earmark_status(mark["id"]) == "expired"   # still permanently lapsed
    assert ledger.is_earmarked("K010") is False


def test_expire_stale_earmarks_can_be_scoped_to_one_project(ledger, clock):
    p1 = ledger.declare_project("bob", "cold frame")
    p2 = ledger.declare_project("carol", "bike trailer")
    ledger.earmark(p1["id"], "K010", "erin")
    ledger.earmark(p2["id"], "K020", "dana")

    clock.advance(timedelta(days=2))
    lapsed = ledger.expire_stale_earmarks(project_id=p1["id"])

    assert len(lapsed) == 1
    assert lapsed[0]["data"]["project_id"] == p1["id"]
    assert ledger.balance("erin") == CREDIT_PER_LAPSE_V0
    assert ledger.balance("dana") == 0   # untouched -- different project, not scanned
