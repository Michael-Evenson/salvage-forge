# Benchmark: cloud vs local intake (claude-sonnet vs qwen3-vl:8b)

Roadmap item #2. One photo, one run per backend, so treat this as a
first data point that surfaced real integration bugs — not a verdict on
model quality. The three gotchas below cost more engineering time than
the photo itself.

**Photo:** `IMG_0531.jpg` (1050x1400) — a wire spool and a corrugated
box on a couch.
**Cloud:** `claude-sonnet-4-6` via the Anthropic API.
**Local:** `qwen3-vl:8b` via Ollama (`localhost:11434`), after the fixes
below (`num_ctx:8192`, `num_predict:10000`, `think:false`).

**Caveat that affects how to read the results below:** the two runs hit
`library.json` in different states. The cloud run saw the box for the
first time (`NEW ITEM`). The local run happened after the box was
already learned from an earlier test, so it hit tier-1 recognition
(`known: seed-amzn-box`) instead of analyzing it fresh. That's a library
state difference, not a model difference — the box isn't a fair
comparison point. The spool *is*: both backends analyzed it fresh
(tier-2), so the output comparison below focuses there.

## Three integration gotchas (local only)

Getting `--local` to a working state took three separate fixes, each
uncovered by the failure of the one before it. All three are already
committed to `intake/intake.py`; this is the record of *why*.

### 1. Over-reasoning (qwen3-vl is a reasoning model)

qwen3-vl:8b emits a hidden `<think>...</think>` pass before its actual
answer. At the original `num_predict:1600`, the model would spend the
entire token budget reasoning and return `message.content: ""` —
nothing left to write the answer with.

**Fix attempted:** pass `"think": false` (Ollama's documented flag to
skip reasoning) and raise `num_predict` to 4000 as a margin.

### 2. `think:false` is silently ignored

Fix #1 didn't work. Verbose dumps showed the model still emitting a full
`thinking` block regardless of the flag. This turned out to be a known,
open Ollama bug: `qwen3-vl:8b` ships with a chat template that lacks the
thinking-control logic present in the plain `qwen3` models, so
`think:false` is accepted by the API but has no effect on this model —
[ollama/ollama#14798](https://github.com/ollama/ollama/issues/14798).

**Fix attempted:** keep `think:false` in the request (free, and correct
the moment Ollama patches the template) but stop trying to suppress
reasoning — budget for it instead. Raised `num_predict` to 10000.

### 3. The real wall was `num_ctx`, not `num_predict`

Still failing. Verbose dumps showed generation stopping at
**`eval_count` ≈ 2120** regardless of the 10000 `num_predict` budget,
with **`prompt_eval_count` ≈ 1970** — almost the same number.
Ollama's default `num_ctx` is **2048** tokens, and it does not auto-grow.
The image plus the library-index prompt were consuming nearly the whole
default context window before generation even started, so the model was
hitting the **context ceiling**, not the output-length ceiling. Every
`done_reason` on the failing runs read `"length"`.

**Fix:** raise `num_ctx` to 8192 alongside `num_predict:10000`.

**Before/after, same model, same kind of photo:**

| | done_reason | eval_count | outcome |
|---|---|---|---|
| Before (`num_ctx` default 2048) | `"length"` | ~2120 | `content` empty, nothing to parse |
| After (`num_ctx:8192`) | `"stop"` | 3421 | valid JSON, parsed cleanly |

That before/after — same failure mode disappearing the moment `num_ctx`
was raised, with the token counts explaining exactly why — is the
clearest evidence the context window was the actual root cause, not
reasoning length or `think:false`.

## Output comparison (the spool — the fair comparison)

| | Cloud (claude-sonnet) | Local (qwen3-vl:8b) |
|---|---|---|
| Epistemic honesty | `could_be`: ethernet/data cable, coaxial cable · `dims_note`: "~18in dia flange using sofa cushion as scale" · `ask`: "What gauge/type label is printed on the cable jacket?" | `could_be`: cable_spool, electrical_wire · `dims_note`: "spool diameter estimated from couch scale (~12\")" · `ask`: "photo angle showing spool length to confirm footage" |
| Item separation | Spool and box reported as two distinct items, each fully analyzed | Spool and box reported as two distinct items (box via tier-1 hit, see caveat above) |
| Value tier | `resale`, **est $80**, flagged `** HIGH VALUE — HOLD FOR RESALE/CREDIT **` | `reuse`, est $0, `confidence: high` |
| Cost | Not directly instrumented in this run — a single-image Sonnet call, no reasoning-token overhead, returned well within the time it took to read the terminal output | `eval_count: 3421` tokens, `total_duration` ≈ 159.7s |

**Both models held the epistemic-band contract.** Neither presented a
guessed dimension or identity as fact — both used `could_be` for
identity ambiguity, `dims_note` to disclose the size reference used, and
`ask` to name the one photo/question that would resolve the biggest
uncertainty. On this axis, local matched cloud despite running at a
fraction of the parameter count and needing three rounds of config
fixes to produce output at all.

**They diverged hard on value tier — but "diverged" shouldn't be read as
"one of them is wrong."** Same object, same photo: cloud called it
`resale`/$80/HIGH VALUE, local called it `reuse`/$0. It's tempting to
score this as local under-crediting the donor relative to a cloud
"ground truth," but that framing smuggles in an assumption this system
doesn't actually hold: Salvage Forge values materials by their
usefulness as build stock for fabrication, not by resale market price.
Read that way, `resale`/$80 and `reuse`/$0 aren't a correct answer and
a miss — they're two different value axes. Cloud weighted the spool as
a sellable good (intact packaging, branded cable, resale market exists).
Local weighted it as feedstock (wire is reusable for electrical
rough-in and conduit work, without assigning it a market price). The
second framing is arguably the one closer to what this system is
actually for. That doesn't make it "right" either — with n=1 there's no
way to tell whether this was a considered value judgment or an artifact
of qwen3-vl spending most of its token budget on reasoning rather than
value assessment — but it does mean the right lens for judging the gap
is "which value axis does Forge care about," not "which model matched
the other." That question matters for the donor credit ledger on the
roadmap (item #7), which will need to pick one axis (or reconcile both)
before it puts a real number behind this field.

**Cost is the other clear divergence.** 3421 reasoning+answer tokens and
~160 seconds for one item, driven almost entirely by gotcha #2 — reasoning
that can't be turned off and has to be budgeted for instead of
prevented. The cloud call had no visible reasoning-token overhead at
all. That gap is a direct, measurable cost of the `think:false` bug, not
an inherent property of running locally.

## One parser note, unrelated to the model comparison

The local run's response was flagged `"NOTE response truncated — salvaged
complete items only"` by `repair_and_parse`'s truncation heuristic, but
the JSON was in fact complete and parsed correctly. Harmless here
(the salvage path produces the same result as the direct-parse path
when the JSON is actually well-formed) but worth a look — it's a false
positive in the same function [CLAUDE.md](../CLAUDE.md) already flags as
the most fragile seam in the codebase (roadmap item #1, unit tests for
`repair_and_parse`).

## Conclusion: is local viable for this use case?

**Not yet, for anything live or financial — yes, for offline batch
work.**

- **Latency rules out the kiosk path as-is.** ~160s for a single item is
  fine for an unattended overnight batch queue but not for a donor
  standing at a kiosk expecting a passport back in a few seconds. The
  gap is mostly gotcha #2's unremovable reasoning pass, not something
  further tuning of `num_ctx`/`num_predict` will close — it needs either
  Ollama fixing the template bug, a non-reasoning local model, or
  accepting the latency for a batch workflow.
- **The value-tier divergence is an open question, not a defect to
  fix in one backend.** With n=1 there's no basis for treating cloud's
  `resale`/$80 as ground truth and local's `reuse`/$0 as a miss — if
  anything, "reuse as build stock" is closer to what this system exists
  to value than resale price is. Before the donor credit ledger
  (roadmap #7) puts a real number behind this field, it needs to decide
  which axis it's crediting (or how to reconcile both), and it needs
  more than one sample either way. Until then, don't wire *either*
  backend's `value_tier` straight into credit value without a human
  confirming it — which the existing epistemic-bands design already
  supports (`ask` exists for exactly this kind of uncertainty), it just
  isn't being *used* for value tier yet.
- **The epistemic-honesty result is the genuinely good news.** Local
  held the same observed/estimated/ask discipline as cloud despite
  being an 8B local model. That's the property the whole passport
  system depends on, and it survived the backend swap — the two-tier
  design's bet that "any backend behind the same contract works" holds,
  once the backend-specific quirks (this doc) are handled.
- **None of this touches tier-1.** The box's near-instant tier-1 hit in
  the local run cost essentially nothing regardless of backend — the
  two-tier architecture's whole point is that repeat items never pay
  the 160-second tier-2 toll, cloud or local.

**Practical recommendation:** keep cloud as the default for live intake
(current behavior) — latency alone decides that for now. Local is worth
revisiting for a batch/offline mode (e.g. photograph a day's donations,
run analysis overnight) once either Ollama fixes #14798 or a faster
local reasoning model is available. Separately, before `value_tier` from
*either* backend feeds the donor credit ledger, decide which value axis
(resale-market vs. build-reusability) the ledger is actually crediting
— this benchmark's single sample suggests they're not interchangeable —
and require a confirmation step until there's more data to compare
against.
