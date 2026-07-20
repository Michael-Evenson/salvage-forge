"""Unit tests for repair_and_parse's layered JSON recovery (see CLAUDE.md
JSON-robustness contract), plus the intake<->ledger deposit wiring
(docs/ECONOMY.md build stage 3 integration).
Run with: python3 -m pytest intake/test_intake.py -v
"""

import sys
from pathlib import Path

import pytest
from PIL import Image

import intake
from intake import repair_and_parse


def test_markdown_fences_are_stripped():
    # Claude/Ollama both sometimes wrap JSON in ```json ... ``` even when told not to.
    text = '```json\n{"items": [{"name": "pallet", "qty": 2}]}\n```'
    result = repair_and_parse(text)
    assert result == {"items": [{"name": "pallet", "qty": 2}]}


def test_smart_quotes_are_normalized():
    # Some models substitute curly quotes for straight ones, which breaks json.loads outright.
    text = "“items”: [{“name”: “pallet”, “qty”: 1}]"
    text = "{" + text + "}"
    result = repair_and_parse(text)
    assert result == {"items": [{"name": "pallet", "qty": 1}]}


def test_truncated_items_array_salvages_complete_objects():
    # Simulates hitting MAX_TOKENS mid-response: last object is cut off mid-field.
    text = (
        '{"items": ['
        '{"name": "box", "qty": 3}, '
        '{"name": "pallet", "qty": 1}, '
        '{"name": "cut off", "descripti'
    )
    result = repair_and_parse(text)
    assert result["truncated"] is True
    assert result["items"] == [
        {"name": "box", "qty": 3},
        {"name": "pallet", "qty": 1},
    ]


def test_complete_response_is_not_flagged_truncated():
    # Regression test for the false positive seen during qwen3-vl benchmark
    # testing (docs/BENCHMARK.md): a fully complete, valid items array got
    # flagged "truncated" anyway. Root cause: the salvage path is reached
    # whenever the fast-path json.loads fails for ANY reason -- here, a
    # leading preamble with a stray brace breaks the naive first-'{'/
    # last-'}' slice -- but the old code unconditionally stamped
    # truncated=True whenever it salvaged >=1 item, regardless of whether
    # the items array actually closed cleanly.
    text = (
        'Sure, here is the JSON {as requested}: '
        '{"items": ['
        '{"name": "box", "qty": 3}, '
        '{"name": "pallet", "qty": 1}'
        ']}'
    )
    result = repair_and_parse(text)
    assert "truncated" not in result
    assert result["items"] == [
        {"name": "box", "qty": 3},
        {"name": "pallet", "qty": 1},
    ]


def test_pure_garbage_raises_value_error():
    # No '{' anywhere in the text at all.
    with pytest.raises(ValueError):
        repair_and_parse("the model just said something unhelpful in prose")


def test_garbage_with_stray_brace_but_no_items_array_raises_value_error():
    # Has a '{' so the fast-path brace slice is attempted, but there's no valid
    # object, list, or well-formed [items] array to salvage from.
    with pytest.raises(ValueError):
        repair_and_parse("well, { sort of a thought here")


# ---------------------------------------------------------------------------
# Intake <-> Ledger wiring (docs/ECONOMY.md build stage 3 integration).
# main() drives everything off sys.argv and does file I/O relative to the
# working directory, so these run it end-to-end with monkeypatched argv and
# a chdir'd tmp_path -- both intake's own files (library.json/inventory.csv)
# and the ledger's default relative path (ledger/ledger.jsonl) land safely
# inside the sandbox, exactly like ledger/test_ledger.py's own tmp_path use.
# ---------------------------------------------------------------------------

LEDGER_DIR = str(Path(__file__).resolve().parent.parent / "ledger")


def _write_test_photo(path):
    Image.new("RGB", (50, 50), (10, 20, 30)).save(path)


def _open_test_ledger():
    """Same lazy sys.path trick intake.open_ledger() uses -- resolves to the
    identical Ledger class/module main() itself will import, so a fresh
    Ledger() here (called after chdir'ing into the same tmp_path) reads the
    exact file main() just wrote to."""
    if LEDGER_DIR not in sys.path:
        sys.path.insert(0, LEDGER_DIR)
    from ledger import Ledger
    return Ledger()


def test_intake_with_donor_and_no_project_banks_credit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv", ["intake.py", str(photo), "--dry-run", "--donor", "alice"])

    intake.main()

    ledger = _open_test_ledger()
    # dry-run's canned response is 3 items (1 known hit + 2 new) -> 3 rows -> 3 deposits.
    assert ledger.balance("alice") == 3 * intake.CREDIT_PER_ITEM_V0
    deposits = [r for r in ledger.all_records() if r["type"] == "deposit"]
    assert len(deposits) == 3
    assert all(d["data"]["donor"] == "alice" for d in deposits)


def test_intake_with_project_feeds_project_and_resets_activity(tmp_path, monkeypatch):
    # --project alone attributes the deposit and counts as activity -- it
    # does NOT by itself reserve the material (earmark is a separate,
    # deliberate --earmark opt-in; see the next test).
    monkeypatch.chdir(tmp_path)
    setup_ledger = _open_test_ledger()
    project = setup_ledger.declare_project("bob", "cold frame")
    first_activity = setup_ledger.project_last_activity(project["id"])

    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--donor", "bob",
                          "--project", project["id"]])
    intake.main()

    ledger = _open_test_ledger()
    assert ledger.balance("bob") == 0   # fed the project, no credit minted
    earmarks = [r for r in ledger.all_records()
                if r["type"] == "earmark" and r["project_id"] == project["id"]]
    assert len(earmarks) == 0   # attribution only -- material stays fungible
    # The deposits themselves carry project_id, so the project's activity
    # clock still moved forward from its bare declaration -- the generic,
    # type-agnostic tracking docs/ECONOMY.md specifies.
    assert setup_ledger.project_last_activity(project["id"]) > first_activity


def test_intake_with_project_and_earmark_reserves_the_material(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    setup_ledger = _open_test_ledger()
    project = setup_ledger.declare_project("bob", "cold frame")

    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--donor", "bob",
                          "--project", project["id"], "--earmark"])
    intake.main()

    ledger = _open_test_ledger()
    assert ledger.balance("bob") == 0
    earmarks = [r for r in ledger.all_records()
                if r["type"] == "earmark" and r["project_id"] == project["id"]]
    assert len(earmarks) == 3   # one per dry-run row, now genuinely reserved
    for r in earmarks:
        assert ledger.is_earmarked(r["data"]["inventory_ref"]) is True


def test_earmark_without_project_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--donor", "bob", "--earmark"])
    with pytest.raises(SystemExit):
        intake.main()


def test_anonymous_donor_commons_credits_the_commons_pool(tmp_path, monkeypatch):
    # --donor commons is the explicit opt-in for anonymous crediting -- a
    # bare run with no --donor at all still records nothing (see
    # test_intake_without_donor_never_touches_ledger); this is a
    # deliberately different, still-explicit path, not an automatic default.
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--donor", intake.COMMONS_ID])

    intake.main()

    ledger = _open_test_ledger()
    assert ledger.balance(intake.COMMONS_ID) == 3 * intake.CREDIT_PER_ITEM_V0
    deposits = [r for r in ledger.all_records() if r["type"] == "deposit"]
    assert all(d["data"]["donor"] == intake.COMMONS_ID for d in deposits)


def test_commons_cannot_be_used_as_a_project_owner(tmp_path, monkeypatch):
    # The reservation is enforced ledger-side (declare_project()/draw() both
    # reject COMMONS_ID -- see ledger/test_ledger.py's
    # test_commons_cannot_own_a_project/test_commons_cannot_draw for the
    # direct tests of "a human attempting to claim the commons id is
    # rejected"). intake itself never calls declare_project() or draw(), so
    # this test just confirms intake's own ledger surface -- deposit() --
    # doesn't route around that enforcement.
    monkeypatch.chdir(tmp_path)
    ledger = _open_test_ledger()
    from ledger import LedgerError
    with pytest.raises(LedgerError):
        ledger.declare_project(intake.COMMONS_ID, "not a real project")


def test_intake_without_donor_never_touches_ledger(tmp_path, monkeypatch, capsys):
    # Regression: no --donor must behave exactly as before stage-3 wiring
    # existed -- not just "ledger absent so it no-ops," but never imported
    # or opened at all.
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv", ["intake.py", str(photo), "--dry-run"])

    intake.main()

    out = capsys.readouterr().out
    assert "LEDGER" not in out
    assert not (tmp_path / "ledger").exists()
    assert (tmp_path / "inventory.csv").exists()


def test_project_without_donor_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--project", "some-id"])
    with pytest.raises(SystemExit):
        intake.main()


def test_nonexistent_project_warns_but_completes_intake(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv",
                         ["intake.py", str(photo), "--dry-run", "--donor", "carol",
                          "--project", "no-such-project"])

    intake.main()   # must not raise -- fails loudly via a warning, not an exit

    out = capsys.readouterr().out
    assert "LEDGER  WARNING" in out
    assert "no such project" in out
    assert "DONE" in out
    assert (tmp_path / "inventory.csv").exists()

    ledger = _open_test_ledger()
    assert ledger.balance("carol") == 0   # must NOT silently fall back to banking credit


def test_ledger_failure_does_not_lose_intake_work(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    photo = tmp_path / "photo.jpg"
    _write_test_photo(photo)
    monkeypatch.setattr(sys, "argv", ["intake.py", str(photo), "--dry-run", "--donor", "dana"])

    if LEDGER_DIR not in sys.path:
        sys.path.insert(0, LEDGER_DIR)
    import ledger as ledger_module

    def boom(self, *a, **kw):
        raise RuntimeError("simulated write failure")
    monkeypatch.setattr(ledger_module.Ledger, "deposit", boom)

    intake.main()   # must not raise

    out = capsys.readouterr().out
    assert "LEDGER  WARNING: deposit not recorded" in out
    assert "simulated write failure" in out
    assert "DONE" in out
    assert (tmp_path / "inventory.csv").exists()
    assert (tmp_path / "library.json").exists()
