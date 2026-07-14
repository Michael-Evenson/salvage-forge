import React, { useState, useEffect, useRef } from "react";

// =============================================================================
// SALVAGE INTAKE KIOSK — photo -> material passport -> inventory row
//
// Two-tier learning architecture:
//   TIER 1 (fast): match photo against the learned passport LIBRARY.
//           Hit -> stamp "KNOWN ITEM", skip analysis, reuse stored properties.
//   TIER 2 (deep): full vision analysis -> structured material passport
//           (dimensions, composition, structural + thermal properties,
//            hazards, reuse ideas) -> SAVED to library for next time.
//
// The library persists across sessions via window.storage.
// Inventory rows export in the schema matcher.jl expects.
// =============================================================================

const INK = "#22261F";
const CONCRETE = "#EBE9E3";
const PANEL = "#FBFAF7";
const STEEL = "#35566B";
const SAFETY = "#F2B60F";
const GOOD = "#3F7D46";
const RUST = "#A64B2A";

// --- Seed knowledge: common waste-stream items with known properties ---------
const SEED_LIBRARY = [
  {
    id: "seed-mcd-cup",
    name: "McDonald's cold cup (medium, paper)",
    keywords: ["mcdonalds cup", "fast food cup", "paper cold cup", "soda cup"],
    seen: 0,
    passport: {
      category: "bulk", family: "coated_paperboard",
      description: "PE-coated SBS paperboard cold cup, ~21 fl oz",
      length_in: 5.9, width_in: 3.5, condition: "C",
      composition: ["~95% solid bleached sulfate paperboard", "~5% LDPE liner (inside)"],
      structural: "Negligible load capacity; rigid cone geometry; nests densely",
      thermal: "Paper ignition ~450 F; LDPE liner softens ~220-240 F — not food-safe to reheat",
      hazards: "LDPE liner complicates composting/repulping; keep out of papercrete slurry unless shredded fine",
      reuse: ["seed-starter pots", "papercrete feedstock (shredded)", "paint/glue mixing", "insulation void fill (shredded)"]
    }
  },
  {
    id: "seed-amzn-box",
    name: "Amazon shipping box (single-wall corrugate)",
    keywords: ["amazon box", "cardboard box", "shipping box", "corrugated"],
    seen: 0,
    passport: {
      category: "sheet", family: "corrugated",
      description: "Single-wall C-flute kraft corrugated, ~ECT-32",
      length_in: 18, width_in: 14, condition: "B",
      composition: ["kraft linerboard (virgin+recycled fiber)", "starch adhesive", "possible tape/label residue"],
      structural: "ECT-32: ~32 lb/in edge crush; strong in flute direction; fails wet",
      thermal: "Ignition ~430-500 F; excellent kindling — fire risk in bulk storage",
      hazards: "Tape, labels, and staples must be pulled before repulping",
      reuse: ["sheet-good templates", "concrete form liner", "sheet-mulch gardening", "papercrete/cellulose insulation feedstock"]
    }
  },
  {
    id: "seed-gma-pallet",
    name: "GMA wood pallet (48x40)",
    keywords: ["pallet", "wood pallet", "skid", "gma"],
    seen: 0,
    passport: {
      category: "linear", family: "pallet",
      description: "Standard 48x40 stringer pallet; ~13-15 deck boards + 3 stringers",
      length_in: 40, width_in: 3.5, condition: "C",
      composition: ["mixed hardwood/softwood (oak, SYP common)", "helical nails"],
      structural: "~2500 lb static capacity intact; deck boards ~1x4 rough, stringers ~2x4",
      thermal: "Wood ignition ~572 F; check HT stamp (heat-treated) vs MB (methyl bromide — DO NOT burn or use indoors)",
      hazards: "MB-stamped pallets are chemically treated; spill-stained pallets may carry unknowns",
      reuse: ["disassemble to 1x4 stock (see matcher: pallet family)", "skid foundations", "compost bins", "fencing"]
    }
  }
];

// --- Claude API helpers ------------------------------------------------------
async function askClaude(prompt, imageB64, mediaType) {
  const content = [];
  if (imageB64) content.push({ type: "image", source: { type: "base64", media_type: mediaType, data: imageB64 } });
  content.push({ type: "text", text: prompt });
  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "claude-sonnet-4-6", max_tokens: 1000, messages: [{ role: "user", content }] })
  });
  const data = await resp.json();
  const text = (data.content || []).filter(b => b.type === "text").map(b => b.text).join("\n");
  return JSON.parse(text.replace(/```json|```/g, "").trim());
}

const recognizePrompt = (index) => `You are the fast-recognition tier of a salvage intake kiosk.
Known item library (id :: name :: keywords):
${index.map(e => `${e.id} :: ${e.name} :: ${e.keywords.join(", ")}`).join("\n")}

Look at the photo. If the pictured item is clearly one of the known items above, match it.
Respond ONLY with JSON, no other text:
{"match": "<id or null>", "qty": <estimated count of this item in photo>, "condition": "<A|B|C|D>", "note": "<one short line on what you see>"}`;

const analyzePrompt = `You are the deep-analysis tier of a salvage intake kiosk that catalogs waste
materials for reuse in construction and fabrication. Analyze the pictured item(s) and produce a
MATERIAL PASSPORT. Estimate dimensions from visual context. Use known manufacturing facts for
recognizable branded/standard items. Condition scale: A=like new, B=serviceable, C=worn, D=degraded.
Category must be one of: linear (sticks/lumber/pipe), sheet (flat goods), part (discrete component),
bulk (aggregate/feedstock). Family is a short snake_case material class (e.g. framing, conduit,
plywood, corrugated, hdpe, wheel_26).
Respond ONLY with JSON, no other text:
{"name":"<specific item name>","keywords":["<3-5 recognition keywords>"],
"category":"<linear|sheet|part|bulk>","family":"<snake_case>",
"description":"<one line>","length_in":<num>,"width_in":<num>,"qty":<num>,"condition":"<A-D>",
"composition":["<material %s if known>"],
"structural":"<load/strength characteristics, one line>",
"thermal":"<ignition/melting/softening points in F, one line>",
"hazards":"<treatments, coatings, contaminant risks, one line>",
"reuse":["<3-5 reuse ideas>"],"confidence":"<high|medium|low>"}`;

// --- Storage (batched keys per guidance) --------------------------------------
async function loadState() {
  let lib = SEED_LIBRARY, inv = [];
  try { const r = await window.storage.get("library"); if (r) lib = JSON.parse(r.value); }
  catch (e) { /* first run — seed */ }
  try { const r = await window.storage.get("inventory"); if (r) inv = JSON.parse(r.value); }
  catch (e) { /* first run */ }
  return { lib, inv };
}
async function saveLib(lib) { try { await window.storage.set("library", JSON.stringify(lib)); } catch (e) { console.error(e); } }
async function saveInv(inv) { try { await window.storage.set("inventory", JSON.stringify(inv)); } catch (e) { console.error(e); } }

// =============================================================================
export default function SalvageIntakeKiosk() {
  const [lib, setLib] = useState(null);
  const [inv, setInv] = useState([]);
  const [img, setImg] = useState(null);          // {b64, mediaType, url}
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState([]);
  const [result, setResult] = useState(null);    // {passport, tier, stampSeen}
  const [tab, setTab] = useState("intake");
  const fileRef = useRef(null);

  useEffect(() => { loadState().then(({ lib, inv }) => { setLib(lib); setInv(inv); }); }, []);

  const pushLog = (line) => setLog(l => [...l, line]);

  function onPickFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      setImg({ b64: reader.result.split(",")[1], mediaType: f.type || "image/jpeg", url: reader.result });
      setResult(null); setLog([]);
    };
    reader.readAsDataURL(f);
  }

  async function runIntake() {
    if (!img || busy || !lib) return;
    setBusy(true); setResult(null); setLog([]);
    try {
      // ---- TIER 1: recognition against learned library ----
      pushLog("TIER 1  checking " + lib.length + " learned items...");
      const index = lib.map(e => ({ id: e.id, name: e.name, keywords: e.keywords }));
      let rec = null;
      try { rec = await askClaude(recognizePrompt(index), img.b64, img.mediaType); }
      catch (e) { pushLog("TIER 1  parse issue — falling through to analysis"); }

      if (rec && rec.match && lib.some(e => e.id === rec.match)) {
        const entry = lib.find(e => e.id === rec.match);
        pushLog("TIER 1  HIT: " + entry.name);
        pushLog("TIER 2  skipped — properties recalled from library");
        const newLib = lib.map(e => e.id === entry.id ? { ...e, seen: (e.seen || 0) + 1 } : e);
        setLib(newLib); await saveLib(newLib);
        const p = { ...entry.passport, qty: rec.qty || 1, condition: rec.condition || entry.passport.condition, name: entry.name };
        addInventoryRow(p);
        setResult({ passport: p, tier: 1, seen: (entry.seen || 0) + 1, note: rec.note });
      } else {
        // ---- TIER 2: deep analysis, then LEARN ----
        pushLog("TIER 1  no match — new item");
        pushLog("TIER 2  running full material analysis...");
        const p = await askClaude(analyzePrompt, img.b64, img.mediaType);
        const id = "learned-" + Date.now();
        const entry = { id, name: p.name, keywords: p.keywords || [], seen: 1, passport: p };
        const newLib = [...lib, entry];
        setLib(newLib); await saveLib(newLib);
        pushLog("LEARNED  passport saved — next intake of this item skips analysis");
        addInventoryRow(p);
        setResult({ passport: p, tier: 2, seen: 1 });
      }
    } catch (e) {
      pushLog("ERROR  " + (e.message || "analysis failed") + " — try another photo");
    }
    setBusy(false);
  }

  async function addInventoryRow(p) {
    const row = {
      id: "K" + String(Date.now()).slice(-6),
      category: p.category || "part", family: p.family || "misc",
      description: (p.description || p.name || "item").replace(/,/g, ";"),
      length_in: p.length_in || 0, width_in: p.width_in || 0,
      qty: p.qty || 1, condition: p.condition || "C"
    };
    setInv(prev => { const next = [...prev, row]; saveInv(next); return next; });
  }

  async function resetLibrary() {
    setLib(SEED_LIBRARY); setInv([]); setResult(null); setLog([]);
    await saveLib(SEED_LIBRARY); await saveInv([]);
  }

  const csv = ["id,category,family,description,length_in,width_in,qty,condition",
    ...inv.map(r => [r.id, r.category, r.family, r.description, r.length_in, r.width_in, r.qty, r.condition].join(","))].join("\n");

  if (!lib) return <div style={{ fontFamily: "IBM Plex Mono, monospace", padding: 40, color: INK }}>Opening the scale house...</div>;

  // ---------------------------------------------------------------------------
  return (
    <div style={{ minHeight: "100vh", background: CONCRETE, color: INK, fontFamily: "'IBM Plex Sans', system-ui, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@600;800&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;600&display=swap');
        .stamp { animation: stampIn .35s cubic-bezier(.2,1.6,.4,1) both; }
        @keyframes stampIn { from { transform: scale(2.2) rotate(-14deg); opacity: 0; } to { transform: scale(1) rotate(-8deg); opacity: 1; } }
        @media (prefers-reduced-motion: reduce) { .stamp { animation: none; } }
        .tabbtn:focus-visible, .actbtn:focus-visible { outline: 3px solid ${SAFETY}; outline-offset: 2px; }
      `}</style>

      {/* Header: hazard-stripe rule is the visual vocabulary of a materials yard */}
      <header style={{ background: INK, color: PANEL, padding: "14px 20px 12px" }}>
        <div style={{ fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 30, letterSpacing: 1, lineHeight: 1 }}>
          SALVAGE INTAKE <span style={{ color: SAFETY }}>KIOSK</span>
        </div>
        <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11, opacity: .75, marginTop: 3 }}>
          photo in / material passport out — {lib.length} items learned
        </div>
      </header>
      <div style={{ height: 8, background: `repeating-linear-gradient(-45deg, ${SAFETY} 0 12px, ${INK} 12px 24px)` }} />

      {/* Tabs */}
      <nav style={{ display: "flex", gap: 6, padding: "12px 16px 0" }}>
        {["intake", "library", "inventory"].map(t => (
          <button key={t} className="tabbtn" onClick={() => setTab(t)}
            style={{ fontFamily: "'Saira Condensed'", fontWeight: 600, fontSize: 15, letterSpacing: 1, textTransform: "uppercase",
                     padding: "8px 16px", border: `2px solid ${INK}`, borderBottom: "none", cursor: "pointer",
                     background: tab === t ? PANEL : "transparent", color: INK,
                     borderRadius: "6px 6px 0 0" }}>
            {t}{t === "inventory" && inv.length ? ` (${inv.length})` : ""}
          </button>
        ))}
      </nav>

      <main style={{ background: PANEL, border: `2px solid ${INK}`, margin: "0 16px 24px", padding: 16, borderRadius: "0 6px 6px 6px" }}>

        {/* ============ INTAKE ============ */}
        {tab === "intake" && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
            <section>
              <h2 style={h2s}>1 · Photograph the donation</h2>
              <input ref={fileRef} type="file" accept="image/*" capture="environment" onChange={onPickFile} style={{ display: "none" }} />
              <button className="actbtn" onClick={() => fileRef.current && fileRef.current.click()}
                style={{ ...bigBtn, background: img ? PANEL : SAFETY }}>
                {img ? "Retake / choose another photo" : "Take or choose photo"}
              </button>
              {img && <img src={img.url} alt="donation" style={{ width: "100%", marginTop: 10, border: `2px solid ${INK}`, borderRadius: 4, maxHeight: 300, objectFit: "contain", background: CONCRETE }} />}
              <button className="actbtn" onClick={runIntake} disabled={!img || busy}
                style={{ ...bigBtn, marginTop: 10, background: !img || busy ? CONCRETE : INK, color: !img || busy ? "#999" : PANEL }}>
                {busy ? "Working..." : "2 · Run intake"}
              </button>
              {log.length > 0 && (
                <pre style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11.5, background: INK, color: "#CFE3B8", padding: 10, borderRadius: 4, marginTop: 10, whiteSpace: "pre-wrap" }}>
                  {log.join("\n")}
                </pre>
              )}
            </section>

            <section>
              <h2 style={h2s}>Material passport</h2>
              {!result && <div style={{ fontSize: 13, opacity: .6, padding: "30px 10px", textAlign: "center", border: `2px dashed ${INK}30`, borderRadius: 4 }}>
                The passport prints here.<br />Known items skip straight through — that's the learning.</div>}
              {result && <Passport r={result} />}
            </section>
          </div>
        )}

        {/* ============ LIBRARY ============ */}
        {tab === "library" && (
          <div>
            <h2 style={h2s}>Learned items — Tier-1 recognition skips analysis for these</h2>
            {lib.map(e => (
              <div key={e.id} style={{ border: `2px solid ${INK}`, borderRadius: 4, padding: "10px 12px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{e.name}</div>
                  <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11, opacity: .65 }}>{e.passport.family} · {e.passport.category} · {e.keywords.slice(0, 3).join(" / ")}</div>
                </div>
                <div style={{ fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 18, color: e.seen ? STEEL : "#999" }}>
                  {e.seen || 0}× seen{e.id.startsWith("seed") ? " · seeded" : " · learned"}
                </div>
              </div>
            ))}
            <button className="actbtn" onClick={resetLibrary} style={{ ...bigBtn, background: PANEL, borderColor: RUST, color: RUST, marginTop: 6 }}>
              Reset library and inventory
            </button>
          </div>
        )}

        {/* ============ INVENTORY ============ */}
        {tab === "inventory" && (
          <div>
            <h2 style={h2s}>Inventory rows — paste into matcher.jl's inventory.csv</h2>
            {inv.length === 0 && <div style={{ fontSize: 13, opacity: .6 }}>No intakes yet. Every passport adds a row here.</div>}
            {inv.length > 0 && <>
              <pre style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11.5, background: INK, color: PANEL, padding: 10, borderRadius: 4, overflowX: "auto" }}>{csv}</pre>
              <button className="actbtn" onClick={() => navigator.clipboard && navigator.clipboard.writeText(csv)} style={{ ...bigBtn, background: SAFETY }}>
                Copy CSV
              </button>
            </>}
          </div>
        )}
      </main>
    </div>
  );
}

// --- Passport card (the signature element) -----------------------------------
function Passport({ r }) {
  const p = r.passport;
  const rows = [
    ["Class", `${p.category} / ${p.family}`],
    ["Size", `${p.length_in || "?"}" x ${p.width_in || "?"}" · qty ${p.qty || 1} · grade ${p.condition}`],
    ["Composition", (p.composition || []).join(" · ")],
    ["Structural", p.structural],
    ["Thermal", p.thermal],
    ["Hazards", p.hazards],
    ["Reuse", (p.reuse || []).join(" · ")]
  ];
  return (
    <div style={{ position: "relative", border: `2px solid ${INK}`, borderRadius: 4, background: "#FFFDF6", padding: "12px 14px", boxShadow: "3px 3px 0 " + INK }}>
      <div style={{ fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 20, lineHeight: 1.1, paddingRight: 110 }}>{p.name}</div>
      <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11, opacity: .65, marginBottom: 8 }}>{p.description}</div>
      {rows.filter(x => x[1]).map(([k, v]) => (
        <div key={k} style={{ display: "grid", gridTemplateColumns: "92px 1fr", gap: 8, fontSize: 12.5, padding: "5px 0", borderTop: `1px solid ${INK}22` }}>
          <div style={{ fontFamily: "'IBM Plex Mono'", fontWeight: 600, fontSize: 10.5, textTransform: "uppercase", letterSpacing: .5, opacity: .7 }}>{k}</div>
          <div>{v}</div>
        </div>
      ))}
      <div className="stamp" style={{ position: "absolute", top: 10, right: 10, transform: "rotate(-8deg)",
        border: `3px double ${r.tier === 1 ? GOOD : RUST}`, color: r.tier === 1 ? GOOD : RUST,
        fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 13, lineHeight: 1.15,
        padding: "4px 8px", borderRadius: 3, textAlign: "center", background: "#FFFDF690" }}>
        {r.tier === 1 ? <>KNOWN ITEM<br />ANALYSIS SKIPPED<br />seen {r.seen}×</> : <>NEW ITEM<br />ANALYZED + LEARNED</>}
      </div>
      <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 10.5, marginTop: 8, opacity: .6 }}>
        Row added to inventory. {p.confidence ? `Analysis confidence: ${p.confidence}. ` : ""}Dimensions are visual estimates — tape-check structural stock.
      </div>
    </div>
  );
}

const h2s = { fontFamily: "'Saira Condensed'", fontWeight: 600, fontSize: 16, letterSpacing: 1, textTransform: "uppercase", margin: "0 0 10px" };
const bigBtn = { width: "100%", padding: "12px 14px", fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 16, letterSpacing: 1, textTransform: "uppercase", border: "2px solid " + INK, borderRadius: 4, cursor: "pointer", color: INK };
