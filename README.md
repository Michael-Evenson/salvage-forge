# Salvage Forge

**Photograph waste. Get material passports. Get blueprints.**

Salvage Forge inverts the normal construction workflow. Instead of
*design → bill of materials → buy new*, it goes
*inventory of salvage → what can we build? → cut sheets*.

The long-term vision: a civic kiosk that accepts donated waste and salvage
(deconstruction lumber, packaging, industrial offcuts, household goods),
catalogs each item with AI vision, credits donors like sweat equity, and
generates buildable plans — from bike trailers and cold frames up to
geodesic domes — from whatever is actually in the pile.

## How it works

```
photo ──► INTAKE (AI vision) ──► material passports ──► inventory.csv
                                        │
                                 library.json (learned items)
                                        │
             inventory.csv ──► MATCHER (optimization) ──► cut sheets
```

**Two-tier learning intake.** New items get a full vision analysis — a
*material passport* covering estimated dimensions, composition, structural
and thermal properties, hazards, disassembly potential, reuse ideas, and
value tier. Every passport is saved to a growing library, so known items
are recognized on sight and skip analysis entirely. The kiosk gets smarter
and cheaper with every donation.

**Epistemic honesty by design.** Every passport separates what the camera
*observed* (green) from what the model *estimated* (amber) from what
*needs human confirmation* (red). Vision models state inferences as facts
by default; this system refuses to.

**Constraint-solver matching.** Buildable products are parametric templates
(a bill of constraints, not a shopping list). A mixed-integer linear program
(JuMP + HiGHS) assigns real inventory to template slots — classic cutting
stock — producing per-stick cut sheets. Infeasible builds report their
*minimal shortfall*: exactly what the kiosk should ask donors for next.
The flagship template is a 3-frequency geodesic dome (geometry from
*Domebook 2*, 1971) that automatically shrinks its radius to fit the pile.

**Backend-agnostic AI.** The intake calls either the Anthropic API or a
fully local model via Ollama (`--local`) — same code, same prompts, same
library. Private by default, capable on demand.

## Repository layout

| Path | What it is |
|---|---|
| `intake/intake.py` | Production intake classifier (Python; Anthropic or Ollama backends) |
| `matcher/matcher.jl` | Reverse-BOM matching engine (Julia, JuMP, HiGHS) |
| `matcher/sample_inventory.csv` | Sample salvage inventory in the shared schema |
| `kiosk/salvage_intake.jsx` | Kiosk UI prototype (React artifact for claude.ai) |
| `examples/` | Example cut-sheet output |
| `docs/ARCHITECTURE.md` | Design decisions and roadmap |

## Quickstart

**Intake (cloud):**
```bash
pip install requests pillow
export ANTHROPIC_API_KEY=sk-ant-...
python3 intake/intake.py photo.jpg
```

**Intake (local, no cloud):**
```bash
ollama pull qwen3-vl:8b
python3 intake/intake.py photo.jpg --local
```

**Matcher:**
```bash
julia -e 'using Pkg; Pkg.add(["JuMP","HiGHS"])'
cd matcher && julia matcher.jl sample_inventory.csv
```

Run `python3 intake/intake.py photo.jpg --dry-run` to exercise the whole
pipeline without any API.

## Status

Working prototype. See `docs/ARCHITECTURE.md` for the honest limitations
list and roadmap (2D nesting, global build optimization, embedding-based
tier-1 recognition on edge NPUs, donor credit ledger).

## License

MIT
