#!/usr/bin/env python3
"""
SALVAGE INTAKE — production intake classifier (Python, direct API)
==================================================================
Same two-tier learning architecture as the kiosk artifact, with no
middleman: photo -> material passport(s) -> inventory.csv rows.

  TIER 1: recognize against the learned library (library.json) -> skip analysis
  TIER 2: full vision analysis of new items -> passport SAVED to library

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...        # from console.anthropic.com
    python3 intake.py photo.jpg                # run an intake (Anthropic API)
    python3 intake.py photo.jpg --local        # run against local Ollama model
    python3 intake.py photo.jpg --dry-run      # resize/parse pipeline only, no API
    python3 intake.py photo.jpg --verbose      # also print the raw model response
    python3 intake.py photo.jpg --donor alice  # also record a ledger deposit,
                                                # banking credit to "alice"
    python3 intake.py photo.jpg --donor alice --project <id>
                                                # deposit feeds that declared
                                                # project instead of banking
                                                # credit (docs/ECONOMY.md);
                                                # requires --donor, and the
                                                # project must already exist
    python3 intake.py --library                # show what's been learned

Files it maintains (created on first run, in the working directory):
    library.json     the learned material-passport library
    inventory.csv    rows in the schema matcher.jl reads

Files it reads, if present (never required, never invokes Julia itself):
    matcher/shortfall.json   matcher.jl's price signal (docs/ECONOMY.md
                             build stage 1) -- when found, items whose
                             (category, family) match an open shortfall
                             line get flagged "IN DEMAND" in the printed
                             passport, a separate axis from value_tier
                             (see docs/BENCHMARK.md). Override the search
                             path with SHORTFALL_PATH.

Ledger (docs/ECONOMY.md build stage 3), optional -- only touched at all
when --donor is given (see open_ledger()/record_deposit()): a sibling
Python module (ledger/ledger.py), not a hard dependency -- a bare
`python intake.py photo.jpg` with no --donor never imports it, and any
ledger failure (missing module, locked/corrupt log, a --project that
doesn't exist) is caught and warned about, never fatal to the intake run.

Dependencies:  pip install requests pillow pillow-heif
"""

import base64, io, json, os, re, sys, time, random

try:
    import requests
    from PIL import Image
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install requests pillow pillow-heif")

try:
    import pillow_heif
    pillow_heif.register_heif_opener()           # lets Image.open() read iPhone .heic/.heif photos
except ImportError:
    pass                                          # HEIC support just won't be available; JPEG/PNG etc. still work

# Windows consoles default to the cp1252 codepage, which can't encode the box-drawing
# characters in show_passport() (or arbitrary Unicode a model might return in a
# passport field) — force UTF-8 so printing never crashes the pipeline.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"       # vision-capable; swap to a Haiku model to cut cost
# Local backend (Ollama): run `ollama pull qwen3-vl:8b` then use --local.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-vl:8b")
MAX_TOKENS = 1200
LIB_PATH, INV_PATH = "library.json", "inventory.csv"
VERBOSE = "--verbose" in sys.argv[1:]
INV_HEADER = "id,category,family,description,length_in,width_in,qty,condition"

SEED_LIBRARY = [
    {"id": "seed-amzn-box", "name": "Amazon shipping box (single-wall corrugate)",
     "keywords": ["amazon box", "cardboard box", "shipping box"], "seen": 0,
     "passport": {"category": "sheet", "family": "corrugated",
       "description": "Single-wall C-flute kraft corrugated ~ECT-32",
       "length_in": 18, "width_in": 14, "condition": "B",
       "composition": ["kraft linerboard", "starch adhesive"],
       "structural": "~32 lb/in edge crush; strong along flutes; fails wet",
       "thermal": "Ignition ~430-500 F; fire risk in bulk storage",
       "hazards": "Pull tape, labels, staples before repulping",
       "reuse": ["templates", "form liner", "sheet mulch", "insulation feedstock"]}},
    {"id": "seed-gma-pallet", "name": "GMA wood pallet (48x40)",
     "keywords": ["pallet", "wood pallet", "skid"], "seen": 0,
     "passport": {"category": "linear", "family": "pallet",
       "description": "48x40 stringer pallet; ~13 deck boards + 3 stringers",
       "length_in": 40, "width_in": 3.5, "condition": "C",
       "composition": ["mixed hardwood/softwood", "helical nails"],
       "structural": "~2500 lb static intact; boards ~1x4, stringers ~2x4",
       "thermal": "Wood ignition ~572 F; HT stamp ok, MB stamp = do not burn",
       "hazards": "MB-stamped pallets are chemically treated",
       "reuse": ["1x4 stock for matcher", "skid foundations", "compost bins"]}},
]

# ---------------------------------------------------------------- carbon (docs/CARBON.md stage A)
# Per-item carbon content: family -> mass -> kg CO2e, the Salvage half of
# docs/CARBON.md's "content x duration" mechanic. Deliberately PERSISTED
# (unlike the stage-2 demand signal): carbon content is closer to a stable
# material property than a live market snapshot -- more like structural/
# thermal below than like value_tier's demand flag.
#
# kg_per_unit is a MASS-PER-EXISTING-PASSPORT-DIMENSION coefficient, not a
# true material density -- intake has no thickness/cross-section field
# (docs/CARBON.md's "Relationship to existing code" table already names
# this gap), so this multiplies whichever dimension the item's category
# already provides: length_in for linear/bulk, length_in*width_in for
# sheet, a flat per-unit mass for part. "wire" is the one exception: its
# length_in holds FEET, not inches, matching matcher.jl's StockPiece
# convention for :bulk categories (see sample_inventory.csv's own note on
# the same quirk).
#
# kg_co2e_per_kg is a first-pass approximation in the spirit of ICE
# (Inventory of Carbon & Energy, circularecology.com) / EC3
# (buildingtransparency.org) published cradle-to-gate factors --
# ballparked from public secondary sources, NOT looked up against a
# licensed ICE/EC3 dataset. Good enough for internal accounting
# (docs/CARBON.md's "estimation vs verification"); replace with real
# database lookups before any external claim. flavor distinguishes
# sequestered (biogenic, physically locked in the material) from avoided/
# embodied (emissions dodged by not manufacturing virgin replacement) --
# docs/CARBON.md treats conflating them as a greenwashing trap.
CARBON_COEFFICIENTS = {
    "corrugated": {"kg_per_unit": 0.00042, "kg_co2e_per_kg": 0.94, "flavor": "sequestered"},
    "pallet":     {"kg_per_unit": 0.0237,  "kg_co2e_per_kg": 0.45, "flavor": "sequestered"},
    "framing":    {"kg_per_unit": 0.0413,  "kg_co2e_per_kg": 0.42, "flavor": "sequestered"},
    "plywood":    {"kg_per_unit": 0.0045,  "kg_co2e_per_kg": 0.60, "flavor": "sequestered"},
    "osb":        {"kg_per_unit": 0.0044,  "kg_co2e_per_kg": 0.60, "flavor": "sequestered"},
    "window":     {"kg_per_unit": 0.0070,  "kg_co2e_per_kg": 0.85, "flavor": "avoided"},
    "conduit":    {"kg_per_unit": 0.0095,  "kg_co2e_per_kg": 1.55, "flavor": "avoided"},
    "wire":       {"kg_per_unit": 0.0090,  "kg_co2e_per_kg": 2.50, "flavor": "avoided"},
    "hinge":      {"kg_per_unit": 0.08,    "kg_co2e_per_kg": 1.55, "flavor": "avoided"},
    "tarp":       {"kg_per_unit": 1.2,     "kg_co2e_per_kg": 2.50, "flavor": "avoided"},
    "wheel_26":   {"kg_per_unit": 2.0,     "kg_co2e_per_kg": 2.50, "flavor": "avoided"},
    "wheel_20":   {"kg_per_unit": 1.6,     "kg_co2e_per_kg": 2.50, "flavor": "avoided"},
}

def carbon_estimate(passport):
    """Stage A (docs/CARBON.md): deterministic content estimate from the
    coefficient table above -- no model judgment involved, family and the
    dimensions already on the passport are enough. Returns None if the
    family isn't in the table: no coefficient, no claim, never a guessed
    default."""
    coef = CARBON_COEFFICIENTS.get(passport.get("family"))
    if not coef:
        return None
    category = passport.get("category")
    length_in = passport.get("length_in") or 0
    width_in = passport.get("width_in") or 0
    if category in ("linear", "bulk"):
        mass_kg = length_in * coef["kg_per_unit"]
    elif category == "sheet":
        mass_kg = length_in * width_in * coef["kg_per_unit"]
    elif category == "part":
        mass_kg = coef["kg_per_unit"]
    else:
        return None
    if mass_kg <= 0:
        return None
    return {
        "est_carbon_kg_co2e": round(mass_kg * coef["kg_co2e_per_kg"], 3),
        "est_carbon_flavor": coef["flavor"],
        "carbon_note": f"estimated: {passport['family']} coefficient table, "
                        f"~{round(mass_kg, 3)}kg mass basis (first-pass, not "
                        f"audit-grade -- docs/CARBON.md)",
    }

# ---------------------------------------------------------------- image prep
def prep_image(path, max_px=1400, quality=82):
    """Downscale + re-encode to JPEG. Returns (base64_str, info_str)."""
    im = Image.open(path)
    im = im.convert("RGB")                       # strips alpha, handles HEIC via pillow-heif if installed
    im.thumbnail((max_px, max_px))               # in-place, keeps aspect
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    data = buf.getvalue()
    return (base64.b64encode(data).decode(),
            f"{im.width}x{im.height} ({len(data)//1024} KB)")

# ---------------------------------------------------------------- prompt
def scan_prompt(library):
    idx = "\n".join(f"{e['id']} :: {e['name']} :: {', '.join(e['keywords'])}"
                    for e in library)
    return f"""You are a salvage-yard intake analyst cataloging waste materials for reuse in construction/fabrication.

KNOWN LIBRARY (id :: name :: keywords):
{idx}

Examine the photo. Identify up to 3 distinct salvageable ITEMS (ignore furniture, people, pets, background). For EACH item output ONE of:
- If it clearly matches a library entry: {{"known":"<library id>","qty":<count>,"condition":"<A|B|C|D>"}}
- Otherwise a NEW passport (estimate dimensions from context; use known manufacturing facts for recognizable items):
{{"name":"...","keywords":["3 short"],"category":"<linear|sheet|part|bulk>","family":"<snake_case>","description":"...","length_in":0,"width_in":0,"qty":1,"condition":"A-D","composition":["<=3"],"structural":"...","thermal":"ignition/melt F","hazards":"...","reuse":["<=3"],"est_value_usd":0,"value_tier":"<scrap|reuse|resale>","confidence":"high|medium|low"}}

Value tiers: scrap = feedstock only; reuse = useful as building material; resale = sellable as-is (unused/sealed/branded goods). Unopened packaging or shrink wrap suggests grade A and possible resale tier.

EPISTEMIC RULES — follow strictly:
1. Name items by what is VISIBLE, not assumed identity. If purpose/product is ambiguous, use a descriptive name and put specific guesses in "could_be".
2. All dimensions are ROUGH ESTIMATES; state your size reference in "dims_note". Never present exact dimensions as fact.
3. Never claim what you cannot see: package contents, spool footage, hidden faces, exact counts.
4. If identity or size is materially uncertain, fill "ask" with the ONE question or photo angle that would resolve it.
Extra fields per NEW passport: "id_basis":"...","could_be":["<=2"],"dims_note":"...","ask":"..."

Condition: A=like new B=serviceable C=worn D=degraded.
KEEP EVERY STRING UNDER 100 CHARACTERS. Respond with ONLY this JSON, nothing else:
{{"items":[ ... ]}}"""

# ---------------------------------------------------------------- API calls
def call_ollama(prompt, image_b64):
    """Local inference via Ollama's chat API. Same contract as call_claude:
    prompt + base64 image in, raw text out. No key, no cloud, no caps."""
    # qwen3-vl is a reasoning model: by default Ollama has it emit a hidden
    # "thinking" pass before the answer, and that pass counts against
    # num_predict. think:false is the documented way to skip it, but
    # qwen3-vl:8b ships with a broken chat template that ignores think:false
    # entirely (open Ollama bug: github.com/ollama/ollama/issues/14798) --
    # it always thinks. We still pass think:false (free, and correct once
    # Ollama fixes the template).
    #
    # Root cause of the empty-content failures: Ollama's default num_ctx is
    # only 2048 tokens, and the image alone tokenizes to most of that --
    # generation was hitting the CONTEXT window, not num_predict, and
    # done_reason came back "length" with prompt_eval_count already near
    # 2000. num_ctx must be raised explicitly (Ollama does not auto-grow it)
    # to leave room for the prompt/image AND a full thinking+answer pass.
    body = {"model": OLLAMA_MODEL, "stream": False, "think": False,
            "options": {"num_predict": 10000, "num_ctx": 8192, "temperature": 0.2},
            "messages": [{"role": "user", "content": prompt,
                          "images": [image_b64]}]}
    try:
        r = requests.post(OLLAMA_URL + "/api/chat", json=body, timeout=600)
    except requests.exceptions.ConnectionError:
        sys.exit(f"No Ollama server at {OLLAMA_URL} — is it running? (ollama serve)")
    data = r.json()
    if VERBOSE:
        print(f"OLLAMA  raw response: {json.dumps(data)}")
    if "error" in data:
        raise RuntimeError(f"Ollama: {data['error']}")
    # message.thinking holds the reasoning trace (if any slipped through);
    # message.content is the actual answer -- only return content.
    return data.get("message", {}).get("content", "")

def call_claude(prompt, image_b64):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY first (get a key at console.anthropic.com).")
    body = {"model": MODEL, "max_tokens": MAX_TOKENS, "messages": [{
        "role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": prompt}]}]}
    r = requests.post(API_URL, json=body, timeout=120, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"API {r.status_code}: {data['error'].get('message', data['error'])}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

# ---------------------------------------------------------------- parsing
def repair_and_parse(text):
    """Fences -> smart quotes -> brace slice -> per-object salvage.

    The salvage path below is reached whenever the fast-path json.loads
    fails, for ANY reason -- not necessarily because the response was
    truncated (e.g. leading preamble text with a stray brace is enough to
    break the naive first-'{'/last-'}' slice even when the JSON itself is
    complete). So "took the salvage path" is not the same fact as "was
    truncated" -- the salvage loop has to actually check whether the items
    array reached its closing ']' before deciding which one happened.
    """
    t = re.sub(r"```json|```", "", text)
    t = t.replace("\u201c", '"').replace("\u201d", '"').strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1:
        raise ValueError("no JSON object in response")
    if b > a:
        try:
            return json.loads(t[a:b + 1])
        except json.JSONDecodeError:
            pass
    items, depth, obj_start, in_str, esc, closed = [], 0, -1, False, False, False
    start = t.find("[", a)
    if start == -1:
        raise ValueError("unparseable response")
    for i in range(start + 1, len(t)):
        c = t[i]
        if esc: esc = False; continue
        if c == "\\": esc = True; continue
        if c == '"': in_str = not in_str
        if in_str: continue
        if c == "{":
            if depth == 0: obj_start = i
            depth += 1
        if c == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                try: items.append(json.loads(t[obj_start:i + 1]))
                except json.JSONDecodeError: pass
                obj_start = -1
        if c == "]" and depth == 0:
            closed = True   # items array actually closed -- not truncated
            break
    if not items:
        raise ValueError("no complete items salvageable")
    return {"items": items} if closed else {"items": items, "truncated": True}

# ---------------------------------------------------------------- storage
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

# ---------------------------------------------------------------- demand signal (stage 2)
# docs/ECONOMY.md build stage 2: close the value loop. The matcher (Julia)
# writes shortfall.json (stage 1); intake (Python) reads the file -- the
# decoupled "wish-list file" seam the design calls for. Never invokes Julia,
# never crashes if the file is absent or stale: absent just means intake runs
# exactly as it did before stage 2 existed.
def load_shortfall():
    """Find and parse the matcher's price signal. Returns (data, path_used) or
    (None, None) if no candidate parses -- caller falls back to old behavior."""
    candidates = [p for p in [os.environ.get("SHORTFALL_PATH"),
                               "matcher/shortfall.json", "shortfall.json",
                               "../matcher/shortfall.json"] if p]
    for path in candidates:
        data = load_json(path, None)
        if data is not None:
            return data, path
    return None, None

def flatten_shortfall(data):
    """shortfall.json is one record per template, each with its own
    shortfall list; flatten to one list of demand lines, keeping which
    template each came from (for the human-readable "why")."""
    return [{"template": tpl.get("template", "?"), "kind": sl.get("kind"),
             "name": sl.get("name"), "amount": sl.get("amount"),
             "families": sl.get("families", [])}
            for tpl in data.get("templates", []) for sl in tpl.get("shortfall", [])]

def match_demand(category, family, entries):
    """An item is in demand when its (category, family) satisfies an open
    shortfall line. Matcher's `kind` (linear/sheet/part/bulk) is the same
    vocabulary as intake's `category` -- match on both, not family alone,
    so a family-name collision across categories can't false-positive."""
    return [e for e in entries if e["kind"] == category and family in e["families"]]

def fmt_amount(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"

# ---------------------------------------------------------------- ledger (docs/ECONOMY.md stage 3 wiring)
# Closes the Salvage->Ledger seam: a successful intake records a deposit,
# so a donation actually banks credit (or feeds a declared project) rather
# than only happening in ledger/test_ledger.py. Optional by construction --
# --donor is the opt-in (its absence means intake never even imports the
# ledger, not just "runs with it absent"), same decoupled-seam spirit as
# load_shortfall() above.
CREDIT_PER_ITEM_V0 = 1  # flat placeholder credit per inventory row; stage 4
                         # (market pricing, docs/ECONOMY.md) replaces this
                         # with a real valuation. Deliberately NOT derived
                         # from est_value_usd, the demand signal, or the
                         # carbon estimate -- that coupling belongs to
                         # stage 4, not this wiring.

def open_ledger():
    """Lazy, guarded import + construction. ledger/ is a sibling directory,
    not a package intake.py depends on at parse time, so intake.py keeps
    working standalone even if ledger/ is missing or broken -- the ledger
    must stay optional. Only called when --donor is given. Never raises;
    returns None on any failure (missing module, unreadable/corrupt log)."""
    try:
        ledger_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ledger")
        if ledger_dir not in sys.path:
            sys.path.insert(0, ledger_dir)
        from ledger import Ledger
        return Ledger()
    except Exception as e:
        print(f"LEDGER  WARNING: could not open ledger -- {e}")
        return None

def record_deposit(ledger, donor, project_id, rid, category, family):
    """The one seam between intake and the ledger (mirrors the
    call_claude()/call_ollama() backend-contract style: one clear boundary,
    not calls scattered through main()). Never raises -- any failure (a
    --project that doesn't exist, a locked/corrupt log file) is caught here
    and reported as a warning. The passport and inventory.csv row this
    deposit describes are already written regardless of whether this call
    succeeds: the item exists physically whether or not the bookkeeping
    does, so a ledger failure must never lose intake work."""
    if ledger is None:
        return
    try:
        if project_id:
            ledger.deposit(donor, rid, project_id=project_id, category=category, family=family)
            print(f"LEDGER  deposit recorded -- {rid} feeds project {project_id}")
        else:
            ledger.deposit(donor, rid, credit_amount=CREDIT_PER_ITEM_V0,
                            category=category, family=family)
            print(f"LEDGER  deposit recorded -- {donor} banked {CREDIT_PER_ITEM_V0} credit for {rid}")
    except Exception as e:
        print(f"LEDGER  WARNING: deposit not recorded for {rid} -- {e}")

def save_library(lib):
    with open(LIB_PATH, "w") as f: json.dump(lib, f, indent=1)

def append_rows(rows):
    new_file = not os.path.exists(INV_PATH)
    with open(INV_PATH, "a") as f:
        if new_file: f.write(INV_HEADER + "\n")
        for r in rows: f.write(r + "\n")

def mk_row(p):
    """Returns (rid, csv_line) -- the id is exposed (not just embedded in the
    formatted line) so callers can reference the same row elsewhere, e.g. as
    a ledger deposit's inventory_ref (see record_deposit)."""
    rid = "K" + str(int(time.time() * 1000) + random.randint(0, 999))[-6:]
    desc = str(p.get("description") or p.get("name") or "item").replace(",", ";")
    return rid, (f"{rid},{p.get('category','part')},{p.get('family','misc')},{desc},"
                 f"{p.get('length_in',0)},{p.get('width_in',0)},{p.get('qty',1)},{p.get('condition','C')}")

# ---------------------------------------------------------------- passport print
def show_passport(p, tier, seen, demand=None):
    """demand: list of matching shortfall entries from match_demand(), or None/[]
    if no match -- never persisted, computed fresh and shown live only (see
    load_shortfall). Resale-based value_tier and demand-based match are two
    separate axes (docs/BENCHMARK.md); each gets its own stamp, never merged."""
    stamp = f"KNOWN ITEM — ANALYSIS SKIPPED (seen {seen}x)" if tier == 1 else "NEW ITEM — ANALYZED + LEARNED"
    if p.get("value_tier") == "resale":
        stamp += "  ** HIGH VALUE — HOLD FOR RESALE/CREDIT **"
    if demand:
        stamp += "  ** IN DEMAND — FORGE NEEDS THIS **"
    print(f"\n  ┌─ {p.get('name','?')}  [{stamp}]")
    for k in ("description", "id_basis", "could_be", "dims_note", "ask",
              "composition", "structural", "thermal", "hazards", "reuse",
              "carbon_note"):
        v = p.get(k)
        if isinstance(v, list): v = " · ".join(str(x) for x in v)
        if v: print(f"  │ {k:<12} {v}")
    if p.get("value_tier"):
        print(f"  │ {'value':<12} tier: {p['value_tier']}"
              + (f" · est ${p['est_value_usd']}" if p.get("est_value_usd") else ""))
    if demand:
        why = "; ".join(f"{m['template']} needs {fmt_amount(m['amount'])} more {p.get('family','?')}"
                        for m in demand)
        print(f"  │ {'demand':<12} {why}")
    if p.get("est_carbon_kg_co2e") is not None:
        print(f"  │ {'carbon':<12} ~{p['est_carbon_kg_co2e']} kg CO2e "
              f"({p.get('est_carbon_flavor', '?')}, estimated)")
    print(f"  └ {p.get('category','?')}/{p.get('family','?')} · "
          f"{p.get('length_in','?')}\"x{p.get('width_in','?')}\" · qty {p.get('qty',1)} · grade {p.get('condition','?')}")

# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    # --donor/--project take a value; exclude both the flag and its value
    # token from the positional-args list below (minimal hand-parsing,
    # matching this file's existing bare-boolean-flag convention rather
    # than pulling in argparse for two value flags).
    VALUE_FLAGS = ("--donor", "--project")
    consumed = {i + 1 for i, a in enumerate(argv) if a in VALUE_FLAGS and i + 1 < len(argv)}
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and i not in consumed]
    donor = argv[argv.index("--donor") + 1] if "--donor" in argv else None
    project_id = argv[argv.index("--project") + 1] if "--project" in argv else None
    if project_id and not donor:
        sys.exit("--project requires --donor -- who is feeding this project?")

    library = load_json(LIB_PATH, SEED_LIBRARY)

    if "--library" in flags:
        for e in library:
            print(f"  {e.get('seen',0):>3}x  {e['name']}  ({e['passport'].get('family','?')})")
        return

    if not args:
        sys.exit(__doc__)
    photo = args[0]

    b64, info = prep_image(photo)
    print(f"PHOTO   {photo} -> {info}")

    call_model = call_ollama if "--local" in flags else call_claude
    if "--local" in flags:
        print(f"MODE    local via Ollama ({OLLAMA_MODEL} @ {OLLAMA_URL})")

    verbose = "--verbose" in flags

    signal, signal_path = load_shortfall()
    demand_entries = flatten_shortfall(signal) if signal else []
    if signal:
        print(f"DEMAND  read {len(demand_entries)} shortfall line(s) from {signal_path} "
              f"(generated {signal.get('generated_at', '?')})")
    else:
        print("DEMAND  no shortfall.json found -- value_tier stays resale-heuristic only")

    ledger = open_ledger() if donor else None
    if donor:
        dest = f"feeding project {project_id}" if project_id else "banking credit"
        print(f"LEDGER  recording deposits as donor '{donor}' ({dest})")

    if "--dry-run" in flags:
        # Exercise the whole pipeline with a canned response — no API needed.
        # Third item is deliberately value_tier:scrap with family "wheel_20":
        # against the real shortfall.json generated from sample_inventory.csv,
        # its part/wheel_20 shortfall is reliably present (stage-1 tested), so
        # this one item proves the demand-match path with genuine matcher
        # output -- and lands the actual thesis, since a "scrap"-tier item
        # still lighting up "IN DEMAND" is exactly the resale-vs-demand
        # divergence docs/BENCHMARK.md found. "Test widget" below (family
        # test_part) is the free negative-case control -- no real shortfall
        # will ever contain that family.
        raw = ('{"items":[{"known":"seed-amzn-box","qty":2,"condition":"B"},'
               '{"name":"Test widget","keywords":["test"],"category":"part","family":"test_part",'
               '"description":"dry-run item","length_in":10,"width_in":2,"qty":1,"condition":"B",'
               '"composition":["testium"],"structural":"n/a","thermal":"n/a","hazards":"none",'
               '"reuse":["testing"],"est_value_usd":120,"value_tier":"resale","confidence":"high"},'
               '{"name":"20in bike wheel (spare)","keywords":["wheel","spare wheel"],'
               '"category":"part","family":"wheel_20",'
               '"description":"dry-run item -- demonstrates demand-loop match vs real shortfall.json",'
               '"length_in":0,"width_in":0,"qty":1,"condition":"B",'
               '"composition":["rubber tire","steel rim"],"structural":"n/a","thermal":"n/a","hazards":"none",'
               '"reuse":["bike trailer wheel"],"est_value_usd":0,"value_tier":"scrap","confidence":"high"}]}')
        print("DRYRUN  using canned model response")
    else:
        print(f"SCAN    one pass vs {len(library)} learned items...")
        raw = call_model(scan_prompt(library), b64)
    if verbose:
        print(f"RAW     {raw}")

    try:
        parsed = repair_and_parse(raw)
    except ValueError as e:
        print(f"PARSE   {e} — retrying once with strict instruction")
        raw = call_model(scan_prompt(library) + "\n\nOutput ONLY the JSON object.", b64)
        if verbose:
            print(f"RAW     {raw}")
        try:
            parsed = repair_and_parse(raw)
        except ValueError as e2:
            sys.exit(f"PARSE   giving up after retry: {e2}\n{raw[:500]}")

    if parsed.get("truncated"):
        print("NOTE    response truncated — salvaged complete items only")

    rows = []
    for it in parsed.get("items", []):
        if it.get("known") and any(e["id"] == it["known"] for e in library):
            entry = next(e for e in library if e["id"] == it["known"])
            entry["seen"] = entry.get("seen", 0) + 1
            if "est_carbon_kg_co2e" not in entry["passport"]:
                # Backfills SEED_LIBRARY entries and any pre-stage-A library.json
                # the first time they're hit -- persisted onto the STORED entry
                # (not just the local `p` view) so it's computed once, ever.
                est = carbon_estimate(entry["passport"])
                if est: entry["passport"].update(est)
            p = {**entry["passport"], "name": entry["name"],
                 "qty": it.get("qty", 1),
                 "condition": it.get("condition", entry["passport"].get("condition", "C"))}
            rid, row = mk_row(p)
            rows.append(row)
            record_deposit(ledger, donor, project_id, rid, p.get("category"), p.get("family"))
            demand = match_demand(p.get("category"), p.get("family"), demand_entries)
            show_passport(p, 1, entry["seen"], demand)
        elif it.get("name"):
            est = carbon_estimate(it)
            if est: it.update(est)
            eid = f"learned-{int(time.time()*1000)}-{random.randint(0,999)}"
            library.append({"id": eid, "name": it["name"],
                            "keywords": it.get("keywords", []), "seen": 1, "passport": it})
            rid, row = mk_row(it)
            rows.append(row)
            record_deposit(ledger, donor, project_id, rid, it.get("category"), it.get("family"))
            demand = match_demand(it.get("category"), it.get("family"), demand_entries)
            show_passport(it, 2, 1, demand)

    save_library(library)
    if rows:
        append_rows(rows)
        print(f"\nDONE    {len(rows)} row(s) appended to {INV_PATH}; library now {len(library)} items")
    else:
        print("\nEMPTY   no salvageable items identified")

if __name__ == "__main__":
    main()
