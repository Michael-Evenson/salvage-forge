import React, { useState, useEffect, useRef } from "react";

// =============================================================================
// SALVAGE INTAKE KIOSK v0.2 — photo -> material passports -> inventory rows
//
// v0.2 fixes:
//  * ONE vision call scans the whole photo and handles MULTIPLE items:
//    each item either matches the learned library (tier-1 hit) or gets a
//    full new passport (tier-2) — every unknown item is ALWAYS learned.
//  * Hardened JSON parsing: fence stripping, smart-quote repair, brace
//    slicing, and per-object salvage if the response was truncated.
//  * Honest counters: library size and inventory rows shown separately.
// =============================================================================

const INK = "#22261F", CONCRETE = "#EBE9E3", PANEL = "#FBFAF7";
const STEEL = "#35566B", SAFETY = "#F2B60F", GOOD = "#3F7D46", RUST = "#A64B2A";

const SEED_LIBRARY = [
  { id: "seed-mcd-cup", name: "McDonald's cold cup (medium, paper)",
    keywords: ["mcdonalds cup", "fast food cup", "paper cold cup"], seen: 0,
    passport: { category: "bulk", family: "coated_paperboard",
      description: "PE-coated SBS paperboard cold cup ~21 fl oz",
      length_in: 5.9, width_in: 3.5, condition: "C",
      composition: ["~95% SBS paperboard", "~5% LDPE liner"],
      structural: "Negligible load capacity; rigid cone; nests densely",
      thermal: "Paper ignition ~450 F; LDPE softens ~220-240 F",
      hazards: "LDPE liner complicates composting/repulping",
      reuse: ["seed-starter pots", "papercrete feedstock", "mixing cups"] } },
  { id: "seed-amzn-box", name: "Amazon shipping box (single-wall corrugate)",
    keywords: ["amazon box", "cardboard box", "shipping box"], seen: 0,
    passport: { category: "sheet", family: "corrugated",
      description: "Single-wall C-flute kraft corrugated ~ECT-32",
      length_in: 18, width_in: 14, condition: "B",
      composition: ["kraft linerboard", "starch adhesive", "tape residue"],
      structural: "~32 lb/in edge crush; strong along flutes; fails wet",
      thermal: "Ignition ~430-500 F; fire risk in bulk storage",
      hazards: "Pull tape, labels, staples before repulping",
      reuse: ["templates", "form liner", "sheet mulch", "insulation feedstock"] } },
  { id: "seed-gma-pallet", name: "GMA wood pallet (48x40)",
    keywords: ["pallet", "wood pallet", "skid"], seen: 0,
    passport: { category: "linear", family: "pallet",
      description: "48x40 stringer pallet; ~13 deck boards + 3 stringers",
      length_in: 40, width_in: 3.5, condition: "C",
      composition: ["mixed hardwood/softwood", "helical nails"],
      structural: "~2500 lb static intact; boards ~1x4, stringers ~2x4",
      thermal: "Wood ignition ~572 F; HT stamp ok, MB stamp = do not burn",
      hazards: "MB-stamped = chemically treated; avoid stained pallets",
      reuse: ["1x4 stock for matcher", "skid foundations", "compost bins"] } }
];

// --- Hardened JSON extraction -------------------------------------------------
function repairAndParse(text) {
  let t = text.replace(/```json|```/g, "")
              .replace(/[\u201C\u201D]/g, '"').replace(/[\u2018\u2019]/g, "'").trim();
  const a = t.indexOf("{"), b = t.lastIndexOf("}");
  if (a === -1) throw new Error("no JSON object in response");
  if (b > a) { try { return JSON.parse(t.slice(a, b + 1)); } catch (e) { /* fall through */ } }
  // Truncated? Salvage every COMPLETE object inside "items":[ ... ]
  const start = t.indexOf("[", a);
  if (start === -1) throw new Error("unparseable response");
  const items = []; let depth = 0, objStart = -1, inStr = false, esc = false;
  for (let i = start + 1; i < t.length; i++) {
    const c = t[i];
    if (esc) { esc = false; continue; }
    if (c === "\\") { esc = true; continue; }
    if (c === '"') inStr = !inStr;
    if (inStr) continue;
    if (c === "{") { if (depth === 0) objStart = i; depth++; }
    if (c === "}") { depth--; if (depth === 0 && objStart >= 0) {
      try { items.push(JSON.parse(t.slice(objStart, i + 1))); } catch (e) { /* skip bad */ }
      objStart = -1; } }
  }
  if (items.length === 0) throw new Error("no complete items salvageable");
  return { items, truncated: true };
}

async function scanPhoto(prompt, imageB64, mediaType) {
  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "claude-sonnet-4-6", max_tokens: 1000,
      messages: [{ role: "user", content: [
        { type: "image", source: { type: "base64", media_type: mediaType, data: imageB64 } },
        { type: "text", text: prompt }] }] })
  });
  const data = await resp.json();
  if (data.error) throw new Error(data.error.message || "API error");
  return (data.content || []).filter(x => x.type === "text").map(x => x.text).join("\n");
}

// One call, whole scene, mixed known/new. Compact fields so several items fit
// inside the token budget without truncation.
const scanPrompt = (index) => `You are a salvage-yard intake analyst cataloging waste materials for reuse in construction/fabrication.

KNOWN LIBRARY (id :: name :: keywords):
${index.map(e => `${e.id} :: ${e.name} :: ${e.keywords.join(", ")}`).join("\n")}

Examine the photo. Identify up to 3 distinct salvageable ITEMS (ignore furniture, people, pets, room background). For EACH item output ONE of:
- If it clearly matches a library entry: {"known":"<library id>","qty":<count>,"condition":"<A|B|C|D>"}
- Otherwise a NEW passport (estimate dimensions from context; use known manufacturing facts for recognizable items):
{"name":"<specific name>","keywords":["<3 short keywords>"],"category":"<linear|sheet|part|bulk>","family":"<snake_case material class>","description":"<one short line>","length_in":<n>,"width_in":<n>,"qty":<n>,"condition":"<A-D>","composition":["<=3 short entries"],"structural":"<one short line>","thermal":"<ignition/melt points F, short>","hazards":"<short>","reuse":["<=3 short ideas"],"confidence":"<high|medium|low>"}

Condition: A=like new B=serviceable C=worn D=degraded.
KEEP EVERY STRING UNDER 100 CHARACTERS. Respond with ONLY this JSON, nothing else:
{"items":[ ... ]}`;

async function loadState() {
  let lib = SEED_LIBRARY, inv = [];
  try { const r = await window.storage.get("library"); if (r) lib = JSON.parse(r.value); } catch (e) {}
  try { const r = await window.storage.get("inventory"); if (r) inv = JSON.parse(r.value); } catch (e) {}
  return { lib, inv };
}
const saveLib = (lib) => window.storage.set("library", JSON.stringify(lib)).catch(console.error);
const saveInv = (inv) => window.storage.set("inventory", JSON.stringify(inv)).catch(console.error);

// =============================================================================
export default function SalvageIntakeKiosk() {
  const [lib, setLib] = useState(null);
  const [inv, setInv] = useState([]);
  const [img, setImg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState([]);
  const [results, setResults] = useState([]);
  const [tab, setTab] = useState("intake");
  const fileRef = useRef(null);

  useEffect(() => { loadState().then(s => { setLib(s.lib); setInv(s.inv); }); }, []);
  const pushLog = (line) => setLog(l => [...l, line]);

  function onPickFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      setImg({ b64: reader.result.split(",")[1], mediaType: f.type || "image/jpeg", url: reader.result });
      setResults([]); setLog([]);
    };
    reader.readAsDataURL(f);
  }

  function mkRow(p) {
    return { id: "K" + String(Date.now() + Math.floor(Math.random() * 999)).slice(-6),
      category: p.category || "part", family: p.family || "misc",
      description: (p.description || p.name || "item").replace(/,/g, ";"),
      length_in: p.length_in || 0, width_in: p.width_in || 0,
      qty: p.qty || 1, condition: p.condition || "C" };
  }

  async function runIntake() {
    if (!img || busy || !lib) return;
    setBusy(true); setResults([]); setLog([]);
    let newLib = [...lib]; const newRows = []; const cards = [];
    try {
      pushLog("SCAN    one pass, whole photo, vs " + lib.length + " learned items...");
      const index = newLib.map(e => ({ id: e.id, name: e.name, keywords: e.keywords }));
      let parsed = null, raw = "";
      for (let attempt = 1; attempt <= 2 && !parsed; attempt++) {
        try {
          raw = await scanPhoto(scanPrompt(index), img.b64, img.mediaType);
          parsed = repairAndParse(raw);
        } catch (e) {
          pushLog("SCAN    attempt " + attempt + " unparseable (" + e.message + ")" +
                  (attempt === 1 ? " — retrying" : ""));
        }
      }
      if (!parsed) throw new Error("model output unusable after 2 attempts");
      if (parsed.truncated) pushLog("NOTE    response truncated — salvaged complete items only");
      const items = Array.isArray(parsed.items) ? parsed.items : [parsed];
      pushLog("SCAN    found " + items.length + " item(s)");

      for (const it of items) {
        if (it.known && newLib.some(e => e.id === it.known)) {
          // ---- tier-1 hit: recall, don't re-analyze ----
          const entry = newLib.find(e => e.id === it.known);
          entry.seen = (entry.seen || 0) + 1;
          const p = { ...entry.passport, name: entry.name,
                      qty: it.qty || 1, condition: it.condition || entry.passport.condition };
          newRows.push(mkRow(p));
          cards.push({ passport: p, tier: 1, seen: entry.seen });
          pushLog("KNOWN   " + entry.name + " (seen " + entry.seen + "x) — analysis skipped");
        } else if (it.name) {
          // ---- tier-2: new item, ALWAYS learned ----
          const id = "learned-" + Date.now() + "-" + Math.floor(Math.random() * 999);
          newLib.push({ id, name: it.name, keywords: it.keywords || [], seen: 1, passport: it });
          newRows.push(mkRow(it));
          cards.push({ passport: it, tier: 2, seen: 1 });
          pushLog("LEARNED " + it.name + " — new library entry created");
        }
      }
      if (cards.length === 0) pushLog("EMPTY   no salvageable items identified in photo");
      setLib(newLib); saveLib(newLib);
      const nextInv = [...inv, ...newRows];
      setInv(nextInv); saveInv(nextInv);
      setResults(cards);
      if (newRows.length) pushLog("DONE    " + newRows.length + " row(s) added to inventory");
    } catch (e) {
      pushLog("ERROR   " + (e.message || "intake failed") + " — nothing was saved");
    }
    setBusy(false);
  }

  async function resetLibrary() {
    setLib(SEED_LIBRARY); setInv([]); setResults([]); setLog([]);
    saveLib(SEED_LIBRARY); saveInv([]);
  }

  const csv = ["id,category,family,description,length_in,width_in,qty,condition",
    ...inv.map(r => [r.id, r.category, r.family, r.description, r.length_in, r.width_in, r.qty, r.condition].join(","))].join("\n");

  if (!lib) return <div style={{ fontFamily: "IBM Plex Mono, monospace", padding: 40, color: INK }}>Opening the scale house...</div>;

  return (
    <div style={{ minHeight: "100vh", background: CONCRETE, color: INK, fontFamily: "'IBM Plex Sans', system-ui, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@600;800&family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Mono:wght@400;600&display=swap');
        .stamp { animation: stampIn .35s cubic-bezier(.2,1.6,.4,1) both; }
        @keyframes stampIn { from { transform: scale(2.2) rotate(-14deg); opacity: 0; } to { transform: scale(1) rotate(-8deg); opacity: 1; } }
        @media (prefers-reduced-motion: reduce) { .stamp { animation: none; } }
        .tabbtn:focus-visible, .actbtn:focus-visible { outline: 3px solid ${SAFETY}; outline-offset: 2px; }
      `}</style>

      <header style={{ background: INK, color: PANEL, padding: "14px 20px 12px" }}>
        <div style={{ fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 30, letterSpacing: 1, lineHeight: 1 }}>
          SALVAGE INTAKE <span style={{ color: SAFETY }}>KIOSK</span>
        </div>
        <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11, opacity: .75, marginTop: 3 }}>
          library {lib.length} items · inventory {inv.length} rows
        </div>
      </header>
      <div style={{ height: 8, background: `repeating-linear-gradient(-45deg, ${SAFETY} 0 12px, ${INK} 12px 24px)` }} />

      <nav style={{ display: "flex", gap: 6, padding: "12px 16px 0" }}>
        {["intake", "library", "inventory"].map(t => (
          <button key={t} className="tabbtn" onClick={() => setTab(t)}
            style={{ fontFamily: "'Saira Condensed'", fontWeight: 600, fontSize: 15, letterSpacing: 1, textTransform: "uppercase",
                     padding: "8px 16px", border: `2px solid ${INK}`, borderBottom: "none", cursor: "pointer",
                     background: tab === t ? PANEL : "transparent", color: INK, borderRadius: "6px 6px 0 0" }}>
            {t}{t === "library" ? ` (${lib.length})` : ""}{t === "inventory" ? ` (${inv.length})` : ""}
          </button>
        ))}
      </nav>

      <main style={{ background: PANEL, border: `2px solid ${INK}`, margin: "0 16px 24px", padding: 16, borderRadius: "0 6px 6px 6px" }}>
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
              <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 10.5, opacity: .55, marginTop: 6 }}>
                Handles up to 3 distinct items per photo. Every new item is learned.
              </div>
              {log.length > 0 && (
                <pre style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11.5, background: INK, color: "#CFE3B8", padding: 10, borderRadius: 4, marginTop: 10, whiteSpace: "pre-wrap" }}>
                  {log.join("\n")}
                </pre>
              )}
            </section>
            <section>
              <h2 style={h2s}>Material passports</h2>
              {results.length === 0 && <div style={{ fontSize: 13, opacity: .6, padding: "30px 10px", textAlign: "center", border: `2px dashed ${INK}30`, borderRadius: 4 }}>
                Passports print here — one per item found.<br />Known items skip straight through; that's the learning.</div>}
              {results.map((r, i) => <div key={i} style={{ marginBottom: 12 }}><Passport r={r} /></div>)}
            </section>
          </div>
        )}

        {tab === "library" && (
          <div>
            <h2 style={h2s}>Learned items — recognized on sight, analysis skipped</h2>
            {lib.map(e => (
              <div key={e.id} style={{ border: `2px solid ${INK}`, borderRadius: 4, padding: "10px 12px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontWeight: 600 }}>{e.name}</div>
                  <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 11, opacity: .65 }}>{(e.passport.family || "?")} · {(e.passport.category || "?")} · {(e.keywords || []).slice(0, 3).join(" / ")}</div>
                </div>
                <div style={{ fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 18, color: e.seen ? STEEL : "#999" }}>
                  {e.seen || 0}x seen · {e.id.startsWith("seed") ? "seeded" : "learned"}
                </div>
              </div>
            ))}
            <button className="actbtn" onClick={resetLibrary} style={{ ...bigBtn, background: PANEL, borderColor: RUST, color: RUST, marginTop: 6 }}>
              Reset library and inventory
            </button>
          </div>
        )}

        {tab === "inventory" && (
          <div>
            <h2 style={h2s}>Inventory rows — paste into matcher.jl's inventory.csv</h2>
            {inv.length === 0 && <div style={{ fontSize: 13, opacity: .6 }}>No rows yet. Every passport printed on the intake tab adds one row here automatically.</div>}
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

function Passport({ r }) {
  const p = r.passport;
  const rows = [
    ["Class", `${p.category || "?"} / ${p.family || "?"}`],
    ["Size", `${p.length_in || "?"}" x ${p.width_in || "?"}" · qty ${p.qty || 1} · grade ${p.condition || "?"}`],
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
        {r.tier === 1 ? <>KNOWN ITEM<br />ANALYSIS SKIPPED<br />seen {r.seen}x</> : <>NEW ITEM<br />ANALYZED + LEARNED</>}
      </div>
      <div style={{ fontFamily: "'IBM Plex Mono'", fontSize: 10.5, marginTop: 8, opacity: .6 }}>
        Row added to inventory. {p.confidence ? `Confidence: ${p.confidence}. ` : ""}Dimensions are visual estimates — tape-check structural stock.
      </div>
    </div>
  );
}

const h2s = { fontFamily: "'Saira Condensed'", fontWeight: 600, fontSize: 16, letterSpacing: 1, textTransform: "uppercase", margin: "0 0 10px" };
const bigBtn = { width: "100%", padding: "12px 14px", fontFamily: "'Saira Condensed'", fontWeight: 800, fontSize: 16, letterSpacing: 1, textTransform: "uppercase", border: "2px solid " + INK, borderRadius: 4, cursor: "pointer", color: INK };
