"""
Closes the Forge/Matcher->Ledger seam: the mirror of intake.py's
Salvage->Ledger wiring (docs/ECONOMY.md build stage 3). When matcher.jl is
run with a --builder or --contractor identity and completes at least one
build, it writes matcher/draws.json (a structured "builds completed"
artifact, same convention as shortfall.json). This script reads that
artifact and posts each completed build as a ledger.draw() call.

Cross-language seam choice: a file, not a live call. Julia and Python in
this project have never talked directly -- inventory.csv (Python->Julia)
and shortfall.json (Julia->Python) are both files, and matcher.jl already
writes shortfall.json this same way. Shelling out from Julia to a Python
CLI per consumed piece would introduce a new kind of coupling nothing else
here uses, for a workflow that's already batch/discrete (a matcher run
either completes builds or it doesn't -- "more real-time" buys nothing).
So matcher.jl only ever reports facts (build, drawer, consumed piece ids)
into draws.json; this script is the only place that turns those facts into
ledger writes.

Usage:
    julia matcher.jl inventory.csv --builder alice
    python3 ledger/record_draws.py

Same failure-isolation rule as intake.py's ledger wiring: the physical
build and its cut sheet are already on disk (matcher.jl wrote them) before
this script even runs, so nothing here can lose that work. A ledger write
failing for one build -- including insufficient credit, which is just
another LedgerError from draw()'s own balance check, not special-cased
here -- is caught, printed as a warning, and does not stop the rest of the
run.

Idempotency: draws.json is deleted after a processing pass (successful or
not) so re-running this script without a fresh matcher run finds nothing
to do -- otherwise the same builds would be posted, and credit debited,
a second time. Exception: if opening the ledger itself fails before any
entry is processed, the artifact is left in place so the run can be
retried once the underlying issue is fixed.
"""

import json
import os
import sys

from ledger import Ledger

# CWD-relative, same convention as Ledger()'s own default path
# ("ledger/ledger.jsonl") and matcher.jl's shortfall.json/cutsheets.txt --
# this script, like intake.py and matcher.jl, is expected to be run from
# the repo root (see module docstring's Usage).
DRAWS_PATH = "matcher/draws.json"

CREDIT_PER_BUILD_V0 = 1  # flat placeholder credit per completed build; stage 4
                          # (market pricing, docs/ECONOMY.md) replaces this.
                          # Per-build, not per-piece consumed, matching the
                          # one-draw-record-per-build granularity below --
                          # deliberately NOT derived from shortfall/scarcity,
                          # same discipline as intake.py's CREDIT_PER_ITEM_V0.


def record_draws(ledger, path=DRAWS_PATH):
    """Reads a draws.json artifact and posts one ledger.draw() per completed
    build. Returns (recorded, failed) counts. Never raises for a per-build
    failure -- each entry is attempted independently and a failure is
    printed as a warning, not propagated. Deletes the artifact on the way
    out (see module docstring for why)."""
    if not os.path.exists(path):
        print(f"LEDGER  no draws.json found at {path} -- nothing to record")
        return 0, 0

    with open(path, encoding="utf-8") as f:
        artifact = json.load(f)

    drawer = artifact["drawer"]
    builds = artifact.get("builds", [])
    recorded, failed = 0, 0
    for build in builds:
        template = build["template"]
        consumed_ids = build.get("consumed_ids", [])
        try:
            ledger.draw(drawer["id"], drawer["role"], template, CREDIT_PER_BUILD_V0,
                        job_id=drawer.get("job_id"), client=drawer.get("client"),
                        consumed_ids=consumed_ids)
            print(f"LEDGER  draw recorded -- {drawer['id']} drew {CREDIT_PER_BUILD_V0} "
                  f"credit for '{template}' ({len(consumed_ids)} piece(s) consumed)")
            recorded += 1
        except Exception as e:
            # Broad on purpose, same as intake.py's record_deposit(): a
            # business-rule violation (insufficient credit, a malformed
            # role) and an infrastructure failure (locked/corrupt log file)
            # are both just "this particular draw didn't get recorded" from
            # here -- neither should stop the rest of the run.
            print(f"LEDGER  WARNING: draw not recorded for '{template}' -- {e}")
            failed += 1

    os.remove(path)
    return recorded, failed


def open_ledger():
    """Never raises; returns None on any failure (corrupt log, unreadable
    file) so a broken ledger can't take down this script before it even
    gets to draws.json -- mirrors intake.py's open_ledger()."""
    try:
        return Ledger()
    except Exception as e:
        print(f"LEDGER  WARNING: could not open ledger -- {e}")
        return None


def main():
    ledger = open_ledger()
    if ledger is None:
        return
    recorded, failed = record_draws(ledger)
    print(f"DONE -- {recorded} draw(s) recorded, {failed} failed")


if __name__ == "__main__":
    sys.exit(main())
