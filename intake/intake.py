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
    python3 intake.py --library                # show what's been learned

Files it maintains (created on first run, in the working directory):
    library.json     the learned material-passport library
    inventory.csv    rows in the schema matcher.jl reads

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
    # "thinking" pass before the answer. That pass counts against num_predict,
    # so a low budget can exhaust itself mid-thought and leave content empty.
    # think:false skips reasoning entirely (cleaner + faster); num_predict is
    # raised as a safety margin in case a future model ignores think:false.
    body = {"model": OLLAMA_MODEL, "stream": False, "think": False,
            "options": {"num_predict": 4000, "temperature": 0.2},
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
    """Fences -> smart quotes -> brace slice -> per-object salvage."""
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
    items, depth, obj_start, in_str, esc = [], 0, -1, False, False
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
    if not items:
        raise ValueError("no complete items salvageable")
    return {"items": items, "truncated": True}

# ---------------------------------------------------------------- storage
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_library(lib):
    with open(LIB_PATH, "w") as f: json.dump(lib, f, indent=1)

def append_rows(rows):
    new_file = not os.path.exists(INV_PATH)
    with open(INV_PATH, "a") as f:
        if new_file: f.write(INV_HEADER + "\n")
        for r in rows: f.write(r + "\n")

def mk_row(p):
    rid = "K" + str(int(time.time() * 1000) + random.randint(0, 999))[-6:]
    desc = str(p.get("description") or p.get("name") or "item").replace(",", ";")
    return (f"{rid},{p.get('category','part')},{p.get('family','misc')},{desc},"
            f"{p.get('length_in',0)},{p.get('width_in',0)},{p.get('qty',1)},{p.get('condition','C')}")

# ---------------------------------------------------------------- passport print
def show_passport(p, tier, seen):
    stamp = f"KNOWN ITEM — ANALYSIS SKIPPED (seen {seen}x)" if tier == 1 else "NEW ITEM — ANALYZED + LEARNED"
    if p.get("value_tier") == "resale":
        stamp += "  ** HIGH VALUE — HOLD FOR RESALE/CREDIT **"
    print(f"\n  ┌─ {p.get('name','?')}  [{stamp}]")
    for k in ("description", "id_basis", "could_be", "dims_note", "ask",
              "composition", "structural", "thermal", "hazards", "reuse"):
        v = p.get(k)
        if isinstance(v, list): v = " · ".join(str(x) for x in v)
        if v: print(f"  │ {k:<12} {v}")
    if p.get("value_tier"):
        print(f"  │ {'value':<12} tier: {p['value_tier']}"
              + (f" · est ${p['est_value_usd']}" if p.get("est_value_usd") else ""))
    print(f"  └ {p.get('category','?')}/{p.get('family','?')} · "
          f"{p.get('length_in','?')}\"x{p.get('width_in','?')}\" · qty {p.get('qty',1)} · grade {p.get('condition','?')}")

# ---------------------------------------------------------------- main
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
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

    if "--dry-run" in flags:
        # Exercise the whole pipeline with a canned response — no API needed.
        raw = ('{"items":[{"known":"seed-amzn-box","qty":2,"condition":"B"},'
               '{"name":"Test widget","keywords":["test"],"category":"part","family":"test_part",'
               '"description":"dry-run item","length_in":10,"width_in":2,"qty":1,"condition":"B",'
               '"composition":["testium"],"structural":"n/a","thermal":"n/a","hazards":"none",'
               '"reuse":["testing"],"est_value_usd":120,"value_tier":"resale","confidence":"high"}]}')
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
            p = {**entry["passport"], "name": entry["name"],
                 "qty": it.get("qty", 1),
                 "condition": it.get("condition", entry["passport"].get("condition", "C"))}
            rows.append(mk_row(p))
            show_passport(p, 1, entry["seen"])
        elif it.get("name"):
            eid = f"learned-{int(time.time()*1000)}-{random.randint(0,999)}"
            library.append({"id": eid, "name": it["name"],
                            "keywords": it.get("keywords", []), "seen": 1, "passport": it})
            rows.append(mk_row(it))
            show_passport(it, 2, 1)

    save_library(library)
    if rows:
        append_rows(rows)
        print(f"\nDONE    {len(rows)} row(s) appended to {INV_PATH}; library now {len(library)} items")
    else:
        print("\nEMPTY   no salvageable items identified")

if __name__ == "__main__":
    main()
