"""
SALVAGE FORGE LEDGER v0 — append-only credit & earmark bookkeeping
====================================================================
docs/ECONOMY.md build stage 3 ("Ledger v0"). Implements the mechanics
from that doc's "Ledger mechanics" and "Ledger implementation" sections
literally: deposit/credit/draw/project/earmark, all as append-only
signed records, balances and earmark status derived by REPLAYING the
log rather than stored as separately-mutable numbers -- that's what
makes "no one can silently rewrite history" true.

Explicitly out of scope for v0 (see docs/ECONOMY.md's build-stage
order): market pricing (credit amounts are caller-supplied, not
computed -- that's stage 4) and the self-specified-vs-market-value
credit split (a natural follow-up once this lands, not built here).
No UI. No payment processing -- cash buy-in is just a manual credit
grant.

"Signed" here means a SHA-256 HASH CHAIN (each record's hash covers its
own content plus the previous record's hash, so editing any past line
breaks every hash after it) -- tamper-EVIDENT, not signed by an
identified party via asymmetric cryptography. That's the right bar for
a single local operator's own file; real per-party signing becomes
relevant once there's more than one write-authority, which is exactly
the multi-community-federation case docs/ECONOMY.md already defers.

Backend-agnostic by construction, same discipline as call_claude()/
call_ollama()'s (prompt, image) -> text contract (CLAUDE.md contract
#2): a backend need only implement append(record) and read_all(). v0
ships JsonlFileBackend; a future distributed backend implements the
same two methods and nothing in Ledger changes.

COMMONS_ID ("commons") is a reserved actor id for anonymous-donation
credit -- a first-class collective-pool recipient, not a person. It can
receive credit like anyone else; declare_project()/draw() both reject
it, since owning a project or drawing materials implies individual
accountability the pool doesn't have. Disbursing accumulated commons
credit is deliberately left as a future governance decision, not built
here.

deposit()'s project_id attributes a deposit and counts as project
activity (resets the earmark clock for that project) WITHOUT reserving
the deposited inventory_ref -- that's a separate, explicit earmark=True.
Attribution and reservation used to be conflated (feeding a project
always earmarked); they're deliberately decoupled now, since locking
stock other builds could use is a real claim that should never be a
side effect of merely naming which project a deposit is for.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

DEFAULT_PATH = "ledger/ledger.jsonl"
DEFAULT_INACTIVITY_WINDOW = timedelta(days=90)   # tunable; see Ledger(inactivity_window=...)

DRAW_ROLES = {"builder", "contractor"}
CREDIT_SOURCES = {"donation", "manual_grant"}

COMMONS_ID = "commons"   # reserved: the collective pool anonymous deposits
                          # credit to (docs/ECONOMY.md's nonprofit mechanism
                          # -- commons credit accumulates, disbursement is an
                          # operator decision left for later). It can RECEIVE
                          # credit like any recipient, but is not a person:
                          # it cannot own a project or draw materials -- see
                          # declare_project()/draw() below. Reserving it here,
                          # not just as an intake-side convention, is what
                          # makes "a human can't register commons as a
                          # personal account" actually enforced rather than
                          # a documentation-only convention any caller could
                          # ignore.


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _parse(iso_ts):
    return datetime.fromisoformat(iso_ts)


def _canonical(obj):
    """Stable serialization for hashing -- sorted keys, no incidental
    whitespace, so the same record always hashes the same way."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class JsonlFileBackend:
    """Append-only local file backend for Ledger v0. One JSON object per
    line. append() only ever opens in 'a' mode -- structurally incapable
    of overwriting a previous line, not just conventionally so."""

    def __init__(self, path):
        self.path = path

    def append(self, record):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def read_all(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


class LedgerError(ValueError):
    """Raised for ledger business-rule violations (insufficient balance,
    earmark against a nonexistent project, a contractor draw missing
    job/client attribution, etc.) -- always ValueError-compatible so
    existing broad `except ValueError` handling elsewhere still works."""


class Ledger:
    def __init__(self, path=DEFAULT_PATH, backend=None, inactivity_window=DEFAULT_INACTIVITY_WINDOW,
                 clock=_now):
        self.backend = backend or JsonlFileBackend(path)
        self.inactivity_window = inactivity_window
        self._clock = clock   # injectable for deterministic tests of time-based earmark expiry
        self._last_hash = None
        self._seq = 0
        for r in self.backend.read_all():
            self._last_hash = r["hash"]
            self._seq = r["seq"] + 1

    # ---------------------------------------------------------------- core append
    def _append(self, type_, data, project_id=None, record_id=None):
        record = {
            "seq": self._seq,
            "id": record_id or str(uuid.uuid4()),
            "ts": _iso(self._clock()),
            "type": type_,
            "project_id": project_id,
            "data": data,
            "prev_hash": self._last_hash,
        }
        record["hash"] = hashlib.sha256(_canonical(record)).hexdigest()
        self.backend.append(record)
        self._last_hash = record["hash"]
        self._seq += 1
        return record

    def _replay(self):
        return list(self.backend.read_all())

    # ---------------------------------------------------------------- deposit / credit
    def deposit(self, donor, inventory_ref, credit_amount=0, category=None,
                family=None, project_id=None, earmark=False):
        """Materials arrive -> an inventory record enters the reservoir.
        Either feeds the donor's own declared project (project_id given --
        no credit minted) or banks credit (project_id omitted --
        credit_amount is minted to the donor). credit_amount is
        caller-supplied; the ledger does not compute a valuation (stage 4,
        out of scope for v0).

        Feeding a project attributes the deposit to it and counts as
        project activity (project_id is set on the deposit record itself,
        so it resets the inactivity clock for that project's existing
        earmarks) -- it does NOT by itself reserve this inventory_ref.
        Reservation is a separate, deliberate act: pass earmark=True to
        also earmark this inventory_ref against the project. Locking
        stock other builds could use, and starting lease obligations,
        should be a deliberate claim, not a side effect of attribution.

        project_id is validated to exist regardless of earmark -- that
        check used to happen implicitly, as a side effect of always
        calling earmark() (which validates it internally). Decoupling
        earmark from deposit removed that free ride, so it's checked
        explicitly here now: a deposit attributed to a project is exactly
        as much a claim about that project's existence as reserving
        material against it is, and both should fail the same way for a
        bogus project_id, not silently diverge."""
        if project_id is not None and credit_amount:
            raise LedgerError("deposit feeds a project OR banks credit, not both")
        if project_id is not None and self._find_project(project_id) is None:
            raise LedgerError(f"no such project: {project_id} -- no project, no deposit")
        deposit_record = self._append("deposit", {
            "donor": donor, "inventory_ref": inventory_ref,
            "category": category, "family": family,
            "credit_amount": credit_amount,
        }, project_id=project_id)
        if project_id is not None:
            if earmark:
                self.earmark(project_id, inventory_ref)
        elif credit_amount:
            self.grant_credit(donor, credit_amount, source="donation",
                               reference=deposit_record["id"])
        return deposit_record

    def grant_credit(self, recipient, amount, source="manual_grant", reference=None, note=None):
        """Credit is permanent and never expires. Earned by donating
        (deposit() calls this internally with source="donation") or
        granted directly -- cash buy-in is a manual grant here; no
        payment processing is implemented."""
        if source not in CREDIT_SOURCES:
            raise LedgerError(f"unknown credit source: {source}")
        return self._append("credit", {
            "recipient": recipient, "amount": amount,
            "source": source, "reference": reference, "note": note,
        })

    # ---------------------------------------------------------------- draw
    def draw(self, drawer, role, inventory_ref, credit_amount, project_id=None,
              job_id=None, client=None, consumed_ids=None):
        """A builder or contractor pulls materials, paying credits.
        Contractors must attribute the draw to a job/client (docs/ECONOMY.md
        stakeholder classes); builders don't carry that attribution.

        inventory_ref is a single human-readable "what was drawn" (e.g. a
        stock piece id for a single-piece draw, or a build/template name
        for a whole-build draw -- see matcher/record_draws.py, the
        Forge/Matcher-side wiring). consumed_ids is an optional additive
        field for the latter case: the specific stock piece ids a
        multi-piece draw consumed, kept for traceability without forcing
        one ledger record per piece. Defaults to empty so existing
        single-piece callers are unaffected."""
        if drawer == COMMONS_ID:
            raise LedgerError(f"'{COMMONS_ID}' is reserved for the collective pool and "
                               "cannot draw materials -- disbursing commons credit is a "
                               "future governance decision, not an individual draw")
        if role not in DRAW_ROLES:
            raise LedgerError(f"unknown draw role: {role} (must be one of {DRAW_ROLES})")
        if role == "contractor" and not (job_id and client):
            raise LedgerError("a contractor draw must be attributed to a job_id and client")
        if role == "builder" and (job_id or client):
            raise LedgerError("job_id/client are contractor-only attribution")
        bal = self.balance(drawer)
        if credit_amount > bal:
            raise LedgerError(f"{drawer} has {bal} credit, cannot draw {credit_amount}")
        return self._append("draw", {
            "drawer": drawer, "role": role, "inventory_ref": inventory_ref,
            "credit_amount": credit_amount, "job_id": job_id, "client": client,
            "consumed_ids": list(consumed_ids or []),
        }, project_id=project_id)

    def record_certified_work(self, contractor, job_id, client, description,
                               draw_ids=None, project_id=None):
        """The durable certified-work attestation docs/ECONOMY.md calls
        for -- separate from the draw records themselves, doubling as a
        reputation/verification trail. Labor is never priced; nothing
        here carries an hours or dollar-for-labor field."""
        return self._append("certified_work", {
            "contractor": contractor, "job_id": job_id, "client": client,
            "description": description, "draw_ids": list(draw_ids or []),
        }, project_id=project_id)

    # ---------------------------------------------------------------- project / earmark
    def declare_project(self, owner, name):
        if owner == COMMONS_ID:
            raise LedgerError(f"'{COMMONS_ID}' is reserved for the collective pool and "
                               "cannot own a project -- it's not a person")
        # A project's own id doubles as its project_id from the start, so
        # its declaration counts as its own first activity event -- both
        # have to be decided before the record is built (and hashed), not
        # patched in afterward, since append-only means nothing written
        # can be edited once it's on disk.
        project_id = str(uuid.uuid4())
        return self._append("project", {"owner": owner, "name": name, "status": "declared"},
                             project_id=project_id, record_id=project_id)

    def update_project_status(self, project_id, status, note=None):
        if self._find_project(project_id) is None:
            raise LedgerError(f"no such project: {project_id}")
        return self._append("project_status", {"status": status, "note": note},
                             project_id=project_id)

    def earmark(self, project_id, inventory_ref):
        """Reserves specific inventory against a declared project. No
        project, no earmark. No separate "release" event is ever
        written -- expiry is a pure function of time (see
        earmark_status), not a mutation."""
        if self._find_project(project_id) is None:
            raise LedgerError(f"no such project: {project_id} -- no project, no earmark")
        return self._append("earmark", {"inventory_ref": inventory_ref}, project_id=project_id)

    def _find_project(self, project_id):
        for r in self._replay():
            if r["type"] == "project" and r["id"] == project_id:
                return r
        return None

    def project_last_activity(self, project_id):
        """Generic activity signal: ANY record whose project_id matches,
        regardless of type -- deposit, draw, status update, earmark, or
        (once it exists) blueprint production. A future record type
        resets this clock the moment it sets project_id; no changes
        needed here."""
        latest = None
        for r in self._replay():
            if r.get("project_id") == project_id:
                ts = _parse(r["ts"])
                if latest is None or ts > latest:
                    latest = ts
        return latest

    def earmark_status(self, earmark_id, as_of=None):
        as_of = as_of or self._clock()
        earmark_record = None
        for r in self._replay():
            if r["type"] == "earmark" and r["id"] == earmark_id:
                earmark_record = r
                break
        if earmark_record is None:
            raise LedgerError(f"no such earmark: {earmark_id}")
        last_active = self.project_last_activity(earmark_record["project_id"])
        if last_active is None:
            return "expired"
        return "active" if (as_of - last_active) <= self.inactivity_window else "expired"

    def is_earmarked(self, inventory_ref, as_of=None):
        """Whether the reservoir currently treats this item as reserved
        -- true if any earmark record for it is still active. Nothing
        to un-reserve explicitly: once its earmark expires, this simply
        stops returning true."""
        for r in self._replay():
            if r["type"] == "earmark" and r["data"]["inventory_ref"] == inventory_ref:
                if self.earmark_status(r["id"], as_of=as_of) == "active":
                    return True
        return False

    # ---------------------------------------------------------------- balances / introspection
    def balance(self, who):
        bal = 0
        for r in self._replay():
            if r["type"] == "credit" and r["data"]["recipient"] == who:
                bal += r["data"]["amount"]
            elif r["type"] == "draw" and r["data"]["drawer"] == who:
                bal -= r["data"]["credit_amount"]
        return bal

    def all_records(self):
        return self._replay()

    def verify_chain(self):
        """Confirms tamper-evidence: replays the whole log and checks
        every record's hash matches its recomputed content, and every
        prev_hash matches the previous record's actual hash. Returns
        True if the chain is intact; raises LedgerError naming exactly
        where it broke otherwise."""
        prev_hash = None
        for r in self._replay():
            if r["prev_hash"] != prev_hash:
                raise LedgerError(f"chain broken at seq {r['seq']}: prev_hash mismatch")
            claimed_hash = r["hash"]
            body = {k: v for k, v in r.items() if k != "hash"}
            recomputed = hashlib.sha256(_canonical(body)).hexdigest()
            if recomputed != claimed_hash:
                raise LedgerError(f"chain broken at seq {r['seq']}: content does not match its hash")
            prev_hash = claimed_hash
        return True
