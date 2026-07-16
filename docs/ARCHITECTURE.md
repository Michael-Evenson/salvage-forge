# Architecture

## The inversion

Conventional building: design → bill of materials → procure new materials.
Salvage Forge: existing materials → constraint matching → design that fits.
This is "resource-driven design" — an active research area (see Certain
Measures' *Mine the Scrap*; the "D5" reuse workflow literature) — with a
civic-intake twist: donations, credit accounting, and a catalog spanning
small products (code-exempt) up to structures.

## Two-tier intake ("System 1 / System 2")

- **Tier 1 — recognition.** Match the photo against the learned library
  (`library.json`). A hit reuses the stored passport: near-zero cost, no
  fresh hallucination surface. Currently one cheap model call with the
  library index in the prompt; at scale this becomes image embeddings +
  nearest-neighbor, which runs on edge NPUs (e.g. Hailo on a Raspberry Pi)
  fully offline.
- **Tier 2 — analysis.** Full vision analysis producing a material
  passport. Output is saved as a new library entry, so every stranger who
  pays the analysis toll makes the next encounter free.
- **Curation.** Learning can learn wrong; the UI includes per-entry
  "Forget". Human-editable memory is a feature.

## Epistemic bands

Vision-language models state inferences as observations. Every passport is
therefore structurally split:

- **Observed (green):** only what is directly visible (identifying
  evidence, concrete camera facts, count, condition grade).
- **Estimated (amber):** model judgment — dimensions (with the size
  reference used), composition, structural/thermal properties, hazards,
  disassembly estimate, value tier. Correct or not, these are knowledge
  applied to a guess, and are labeled as such.
- **To confirm (red):** the single question or photo angle that would
  resolve the largest uncertainty. Uncertainty becomes a workflow step,
  not a hidden failure.

## Matching engine

Templates are parametric constraint sets ("20–36 struts, any rigid linear
member ≥ 1.5x1.5 in section"), not fixed part lists. Solved per template as
MILP (JuMP + HiGHS):

- `x[i,k] ∈ Z+` — cuts of demand *k* from stock piece *i*
- `y[i] ∈ {0,1}` — piece *i* consumed
- `s[k] ∈ Z+` — shortfall slack
- minimize `BIG·Σs + Σy` s.t. per-piece capacity (incl. saw kerf) and
  demand satisfaction

Shortfall slack doubles as the kiosk "wish list." The geodesic dome
template searches its own radius: the structure shrinks to fit the pile.
Sequential planning re-solves against the depleted pool after each build.

## Backend-agnostic inference

`call_claude()` and `call_ollama()` honor one contract: (prompt, image) →
raw text. Nothing else in the system knows which brain answered. Swapping
providers is one new function, zero other changes.

## Hard-won engineering lessons (kept on purpose)

1. **Layered JSON recovery:** fence-strip → smart-quote repair → brace
   slice → per-object salvage of truncated arrays → one strict retry →
   fail loudly with state intact. Brittle parsing turns a 95% model
   success into a 100% product failure.
2. **Label errors by layer** (BRIDGE / API / EMPTY / PARSE) — identical
   retry failures mean the request is wrong, not the response handling.
3. **Resize client-side** before upload; transport layers have undocumented
   payload ceilings. Adaptive size ladders beat fixed sizes.
4. **Count physical objects, not material types** — adjacent same-texture
   objects are the hard case for single-pass detection; intake stations
   should instruct spacing between items.

## Roadmap

- True 2D nesting for sheet goods (guillotine/irregular)
- Global build optimization ("maximize value of everything built") as one MILP
- Embedding-based tier-1 on edge NPU; kiosk web UI on Raspberry Pi
- Donor credit ledger (sweat-equity accounting)
- Confidence-driven clarification loop (act on "ask" fields)
- Structural review pathway for dwelling-scale outputs (graded lumber,
  licensed engineer) — small products (sheds <200 sq ft, trailers, garden
  structures) remain the code-exempt wedge
