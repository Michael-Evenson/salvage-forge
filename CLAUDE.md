# CLAUDE.md — project briefing for Claude Code

## What this project is

Salvage Forge turns photographed waste/salvage into material passports
(AI vision intake) and turns inventories of salvage into buildable plans
with cut sheets (MILP matching). Long-term: a civic donation kiosk with a
donor credit ledger. The developer is a beginner-to-intermediate
programmer (Python/C++/Julia learner, strong hardware/maker background)
— explain non-obvious decisions in comments and PR descriptions.

## Architecture contracts (do not break casually)

1. **Inventory CSV schema** is the interface between intake and matcher:
   `id,category,family,description,length_in,width_in,qty,condition`
   category ∈ {linear, sheet, part, bulk}. Both programs depend on it.
2. **Model backend contract:** `call_claude()` and `call_ollama()` in
   `intake/intake.py` both take (prompt, base64_jpeg) → raw text. Any new
   backend must honor this and be selected in main(), nowhere else.
3. **Epistemic separation:** passports distinguish observed (camera facts)
   / estimated (model judgment) / ask (needs human confirmation). Any new
   passport field must be assignable to one of these bands. Never present
   model estimates as observations, in code or UI.
4. **Two-tier learning:** tier-1 recognition against library.json must
   stay cheap; tier-2 analysis results are always saved to the library.
   Bad learning must remain deletable (Forget).
5. **JSON robustness:** model output parsing must keep the layered
   recovery in `repair_and_parse` (fences → smart quotes → brace slice →
   per-object salvage → strict retry). Never replace with a bare
   json.loads.

## Commands

```bash
# intake pipeline test with no API (canned response)
python3 intake/intake.py <photo.jpg> --dry-run

# real intake: cloud
ANTHROPIC_API_KEY=... python3 intake/intake.py <photo.jpg>

# real intake: local (Ollama must be running; qwen3-vl:8b pulled)
python3 intake/intake.py <photo.jpg> --local

# matcher
cd matcher && julia matcher.jl sample_inventory.csv
```

Runtime files `library.json`, `inventory.csv`, `cutsheets.txt` are
gitignored on purpose (user data / generated output).

## Style

- Python: stdlib + requests + pillow only; keep intake.py a single
  readable file until it genuinely hurts.
- Julia: JuMP + HiGHS; comment the MILP formulation when changing it.
- Prefer small, explained commits; the git history is a learning record.

## Roadmap (good next tasks, roughly in order)

1. Unit tests for `repair_and_parse` (truncation, fences, smart quotes,
   garbage) — pytest; this is the most fragile seam.
2. Cloud-vs-local benchmark: run the same photo set through both
   backends, diff passports, write results to docs/.
3. Confidence loop: when a passport has an `ask`, prompt the user for the
   answer and merge it into the library entry.
4. Tier-1 via image embeddings (CLIP) instead of prompt-listing the
   library index; target: runs on Raspberry Pi 5 + Hailo NPU.
5. Flask kiosk UI for the Pi (photo upload → passports in browser);
   replaces the claude.ai artifact in kiosk/.
6. matcher: real 2D nesting for sheet goods; global multi-build MILP.
7. Donor credit ledger schema (donations, credits, value tiers).
