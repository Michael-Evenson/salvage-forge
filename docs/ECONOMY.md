# The Salvage Forge Economy

Architecture/design document. No code lives here — this is the spine
every build stage serves. If a future feature doesn't fit one of the
four phases below, that's a signal to revisit the feature, not to add a
fifth phase.

## The reframe

Salvage Forge is not "photograph waste, get a blueprint." It's a
**materials commons with an internal economy**: donors deposit salvaged
materials and earn credit; builders spend credit to draw materials from
a shared reservoir for their own projects; a matching engine prices the
exchange by reconciling supply against demand; a ledger settles who's
owed what.

The consequence that everything else in this doc follows from: **value
is not a fixed property of an item — it's a market price, set by live
supply and demand, denominated in community credits.** A pallet is worth
more when three people are mid-build on pallet-framed structures than
when the yard already has forty of them sitting unused. No amount of
looking harder at the pallet's photo would tell you that.

This is exactly why intake alone could never assign value correctly.
[`docs/BENCHMARK.md`](BENCHMARK.md) caught this empirically before this
doc named it structurally: on the same photo, cloud and local intake
diverged on value tier — `resale`/$80 vs `reuse`/$0 — and the honest
conclusion there was that neither number is ground truth, because
*resale price* and *build-reusability* are different axes, and neither
is *scarcity relative to current demand*, which is the axis this system
actually needs. A single photo, examined in isolation, cannot know
whether the yard is drowning in pallets or one pallet short of a build.
Only a market — supply meeting demand — can price that. Pricing
requires a market; intake can only ever supply one side of it.

## The four phases

```
MATERIAL FLOW

  DONOR --photograph--> SALVAGE --inventory record--> RESERVOIR --draw--> BUILDER
                        (intake.py)                  (physical stock,          /
                                                        inventory.csv)   CONTRACTOR

DEMAND / PRICE FLOW

  FORGE --demand (build templates)--> MATCHER <--supply-- RESERVOIR
  (matcher.jl                         (matcher.jl:
   templates: dome,                    match_template,
   cold frame,                         shortfall = scarcity
   bike trailer)                          = price signal)

  MATCHER's shortfall feeds back to:
    -> SALVAGE  value_tier reflects real demand, not resale heuristics  (stage 2)
    -> LEDGER   credit value becomes a function of live scarcity        (stage 4)

CO-EVOLUTION  (stage 5)

  SALVAGE <-----------------------------------------------------------> FORGE
  learns what's worth cataloging            proposes new templates from
  from Forge's current catalog              recurring salvage patterns

CREDIT FLOW  (LEDGER, stage 3)

  donor deposit --> credit banked (permanent, never expires)
  builder draw  --> credit spent, balance debited
  earmark       --> leased claim on RESERVOIR stock, tied to an active
                     declared project; expires back to RESERVOIR on
                     inactivity
```

### 1. Salvage — "What I Have"

AI-vision intake (`intake/intake.py`) catalogs materials into inventory
records. It does **not** decide market value — it observes. Its
two-tier design already exists: tier-1 recognizes a photographed item
against the learned library (`library.json`, seeded by `SEED_LIBRARY`)
and skips analysis; tier-2 runs full vision analysis on anything new and
saves the result back into the library, so the next stranger's version
of the same item is a cheap tier-1 hit. That learning loop is coupled to
Forge: Salvage should learn what a new item is worth cataloging *as* by
reading Forge's current catalog of buildable objects — not by guessing
resale value in isolation (see the reframe above, and the gap table
below — this coupling doesn't exist in code yet).

### 2. Forge — "What I Need / What I Can Make"

The build catalog (`matcher/matcher.jl` templates — the self-sizing 3v
geodesic dome, the cold frame, the bike trailer). Each template is a
bill of material *demands*, expressed as constraints
(`LinearDemand`/`SheetDemand`/`PartDemand`), not a fixed shopping list —
`dome_3v(radius_in)` is parametric and `best_dome_radius` searches for
the largest radius the pile actually supports, which is the concrete
code-level meaning of "the dome shrinks to fit the pile." Forge's
learning mirrors Salvage's: it should dream up new templates from what
Salvage keeps seeing come through the door. Neither phase is master —
they co-evolve.

### 3. Matcher — "The Marketplace"

The existing JuMP/HiGHS MILP in `match_template` is not just a solver —
it's the price-discovery mechanism. Its objective is literally
`min 10_000 * Σs[k] + Σy[i]`: satisfy demand first, then use the fewest
pieces. The `s[k]` slack variables — returned today as the `shortfall`
`Dict{String,Int}` — are the scarcity signal that sets price. Something
scarce relative to demand (a shortfall entry with a large unmet count)
is high value; something overflowing and unwanted by any current
template is scrap. No price is ever hand-set anywhere in this system;
the market sets it, by trying to build things and reporting what it's
missing.

### 4. Ledger — "Credit & Debt"

Records deposits, credits, draws, and reservations; settles balances.
The reservoir decouples *contribution* from *consumption* — nobody has
to personally source every part of their own build; they draw against
the pool using credit earned (or bought) from any past contribution.
Does not exist in code yet — this doc's mechanics section below is its
specification.

## Relationship to existing code

| Phase | Exists today | Gap to close |
|---|---|---|
| **Salvage** | Two-tier recognition/learning (`intake.py`): `SEED_LIBRARY` seeds tier-1, tier-2 analysis is saved back to `library.json`. `value_tier` is assigned by prompt heuristic (`scan_prompt`'s "unopened packaging suggests grade A and possible resale tier"). | `value_tier` is resale-heuristic-driven, not demand-driven — the exact gap `docs/BENCHMARK.md` surfaced. Salvage's "KNOWN LIBRARY" index is its own item history, not Forge's build catalog — the cross-phase learning coupling described above doesn't exist. |
| **Forge** | Three hand-written seed templates (`dome_3v`, `cold_frame`, `bike_trailer`) in `matcher.jl`. | Templates are hardcoded Julia functions. Nothing proposes a *new* template from observed salvage patterns — stage 5 below. |
| **Matcher (price discovery)** | Stage 1 shipped ([#6](https://github.com/Michael-Evenson/salvage-forge/pull/6), `8383cac`). `match_template` returns structured `Vector{ShortfallLine}` detail (kind/name/amount/families) for every demand type alongside the original `shortfall::Dict{String,Int}` (unchanged, still drives the stdout wish-list). `BulkDemand` is now a first-class demand type — matched via capacity check like `SheetDemand` (no cutting-stock combinatorics apply to "enough total quantity") — so a `bulk` item (e.g. the wire spool from `docs/BENCHMARK.md`) is visible to matching and can appear in shortfall. A "Utility wire run" template exercises it. `main()` writes the full per-template result set to `matcher/shortfall.json` (gitignored), additive to the existing stdout text. | Nothing reads the artifact yet — Salvage's `value_tier` still comes from the resale heuristic, not this signal (stage 2, below). Also worth naming honestly rather than hiding: bulk quantity reuses `StockPiece.length` (documented inline) rather than a dedicated field, since the CSV schema is a hard interface per `CLAUDE.md` contract #1 — a deliberate reuse, not an oversight, but a real constraint on how bulk data is represented. |
| **Ledger** | Nothing. | Everything: schema, deposit/credit/draw/earmark operations, balance persistence, append-only log — stage 3 below. |

## Ledger mechanics

**Terms.** What intake produces is a *passport* — that word stays
intake's internal term for the analysis output. The moment it enters
the reservoir it becomes an **inventory record**; "inventory," not
"passport," is the economic object the rest of this system operates on.
A donor does not hold a passport — they bank **credit** for the
deposit.

**What's priced.** Materials, and only materials. Labor/hours are not a
currency here. What Forge delivers to a builder is *the build itself* —
instructions plus materials, drawn from the reservoir. This is a
deliberate scope limit: it sidesteps the "how do you value an hour of
labor" problem that has sunk time-banking schemes, by simply never
putting labor on the ledger at all.

**Deposit.** Materials arrive → an inventory record enters the
reservoir → the donor either feeds their own declared build directly,
or banks credit.

**Credit.** Earned by donating, or purchased outright (cash buy-in —
see "real money," below). Spent to draw materials. **Permanent — it
never expires.** Credit is abstract and ties up no physical stock, so
there's no operational reason to expire it; expiring it would only
punish patient donors who haven't found a use for it yet.

**Draw.** A builder pulls materials from the reservoir, paying credits.

**Earmark.** An optional reservation of specific inventory against a
particular build. Requires a declared project — no project, no earmark;
without one, material stays fungible in the shared pool. An earmark is
held only while its project is **active**, where active means *any*
project activity: a deposit, a draw, a status update, or producing a
workable/approvable blueprint. Design progress counts on its own — a
builder still iterating on plans, with no material movement yet, keeps
their reservation. An earmark expires when its project stalls past the
inactivity window, releasing the material back to the reservoir. In
short: **an earmark is a lease, not a deed** — a perishable claim on
physical stock, in contrast to credit, which is a permanent claim on
abstract value.

## Stakeholder classes

- **Donor** — deposits materials; banks credit or feeds their own
  declared build.
- **Builder (DIY)** — draws materials for their own project, paying
  with earned or purchased credit.
- **Contractor (certified/hired)** — a full stakeholder, distinct from
  a DIY builder because they build *for others*, for pay. Their material
  draws are attributed to a job/client, and a **certified-work record**
  is kept — durable and attributable, doubling as a reputation/
  verification trail and giving dwelling-scale or code-relevant builds a
  place to record that a licensed person was involved. Their labor stays
  **off the credit economy**: paid by the client, off-ledger. A
  contractor is accountable to the community and builds standing within
  it — they are not paid *in* it. Like any builder, they must declare
  and keep active a project in order to earmark.

## Honest hard problems

Naming these, not hiding them:

- **Cold-start / bootstrapping.** The economy has no value on day one —
  co-evolution (Salvage learning from Forge's catalog, Forge dreaming up
  templates from salvage patterns) can't bootstrap from two empty
  catalogs; there's nothing to co-evolve *from*. Mitigation: both sides
  ship seeded. Salvage already has `SEED_LIBRARY`; Forge already has its
  three seed templates. Phases 1 and 2 must always ship with starter
  catalogs — this isn't optional polish, it's what makes day-one
  operation possible at all.
- **Credit calibration.** The temptation is to hand-set prices ("a
  pallet is worth 5 credits"). That's the exact failure mode this whole
  design avoids — it's the same category of mistake as intake assigning
  `value_tier` from a resale heuristic. The Matcher-as-marketplace sets
  prices via live supply and demand; nobody sets them by hand.
- **Real money.** The instant credits can be purchased with cash, real
  money enters the system, and that crosses a line this document does
  not resolve: who holds the money, what legal form the operator needs
  to take (nonprofit, co-op, LLC — each has different obligations), and
  whether a purchased credit is a stored-value instrument subject to the
  regulation that implies, including sales tax. This is not a design
  problem this doc can solve — it's a flag: **any deployment that turns
  on cash buy-in needs a real answer to these questions first**, from
  someone qualified to give one.

## Ledger implementation: append-only now, distributed later

The required properties — permanent credits, tamper-evident provenance,
no single party able to silently rewrite history (consistent with this
project's sovereignty-first stance) — do **not** require a blockchain.
They're delivered by a plain **append-only, signed, immutable
transaction log**: every deposit/draw/earmark event is a signed record,
appended, never edited or deleted in place. That gets auditability and
integrity without touching tokens, gas, wallets, or the regulatory
exposure those bring — keeping "Ledger v0" (stage 3, below) genuinely
achievable.

Why blockchain isn't the default, specifically:

1. **Three of the four phases are off-chain regardless.** Matcher is
   heavy MILP computation — not something you'd ever want to run
   on-chain. Salvage is AI vision inference. The reservoir is physical
   material sitting in a shed. A distributed ledger would only ever
   cover the Ledger phase; it buys nothing for the other three.
2. **The oracle problem.** A chain can prove a *credit* moved. It cannot
   prove a *pallet* moved. The instant real material is involved, some
   trusted witness has to attest to the physical fact — and that witness
   is the off-chain intake AI. Trustlessness is punctured exactly where
   the goods are real, which is exactly where it would matter most.
   Putting the ledger on-chain doesn't remove that dependency; it just
   adds ceremony around it.
3. **UX and legal friction.** A retiree donating a pallet should not
   need a wallet and a seed phrase to get credit for it. And the moment
   credit is purchasable, an on-chain token is a *far* bigger legal
   question than a stored-value instrument already is (see "real money,"
   above) — it invites securities-law scrutiny this project has no
   reason to invite this early.

**Where it would earn its complexity:** multi-community federation —
many independent kiosks, each with their own reservoir and ledger,
honoring each other's credits and reputations with no central operator.
A distributed ledger provides credible neutrality a single database
can't, in exactly that scenario. That's a scaling answer, deferred until
more than one community actually exists to federate with. Until then,
the ledger interface should be kept **backend-agnostic**, the same
discipline this codebase already applies to model backends
(`call_claude()`/`call_ollama()` in [`CLAUDE.md`](../CLAUDE.md)'s
contract #2) — so a distributed backend can replace the local one later
without changing anything in the four phases that call it.

## Proposed build-stage order

Each stage below is independently shippable and leaves the system
working on its own — nothing here requires shipping the whole roadmap
before any of it is usable.

1. **Expose the price signal. Shipped** ([#6](https://github.com/Michael-Evenson/salvage-forge/pull/6), `8383cac`).
   `match_template` in `matcher.jl` computed and returned `shortfall`
   already; `main()` still prints it to stdout as kiosk wish-list text,
   unchanged, and now *also* writes the full result set to
   `matcher/shortfall.json` — the structured artifact stage 2 will read.
   Was the smallest possible stage, and everything else depends on it.
   - **Prerequisite this stage surfaced, now satisfied:** "expose the
     price signal" implicitly assumed every inventory category had a
     demand path to be short *against*. `bulk` didn't — `match_template`
     only branched on `:linear` and `:sheet`, so a `bulk` item was
     invisible to shortfall the way a 2x4 or a sheet of plywood wasn't.
     Not a hypothetical gap: the wire spool in `docs/BENCHMARK.md` — the
     exact item whose resale-vs-reuse divergence motivated this whole
     document — was a `bulk` item the matcher structurally could not
     see. Shipped alongside stage 1: `BulkDemand`, a first-class demand
     type for a continuous quantity, folded into the same
     shortfall/feasible mechanism as the other three demand types. Bulk
     items can now appear in `shortfall.json` like any other category.
2. **Close the value loop.** Salvage reads the shortfall artifact from
   stage 1 so `value_tier` reflects live demand instead of the resale
   heuristic in `scan_prompt`. This is the fix to the exact divergence
   `docs/BENCHMARK.md` documented, and the roadmap item already recorded
   in [`CLAUDE.md`](../CLAUDE.md) and
   [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
3. **Ledger v0.** Minimal: deposit banks credit, a build spends it,
   balances persist, append-only log. Deliberately ordered *after*
   stages 1–2, not before: by the time deposits get credited, the item's
   `value_tier` already reflects real demand (a scarcity snapshot at
   intake time), so Ledger v0 launches pricing deposits against a
   genuine signal instead of flat, meaningless credit-per-item numbers.
4. **Market pricing.** Credit value becomes a live function of matcher
   scarcity, rather than the snapshot baked in at intake time in stage
   2 — a build that gets fulfilled changes what's scarce, and unfulfilled
   demand re-prices what's left in the reservoir continuously, not just
   once at intake.
5. **Co-evolution.** Forge proposes new templates from recurring salvage
   patterns — the other half of the co-evolution described in the
   phases above (Salvage's half, learning from Forge's catalog, is
   already how tier-2 learning works structurally; this stage is
   building Forge's half). Left for last deliberately: it's the most
   speculative stage and depends on the first four actually running
   long enough to generate real patterns to learn from.
