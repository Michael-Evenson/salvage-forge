# Carbon Capture: The Other Half of the Mission

Architecture/design document. No code lives here. Peer to
[`docs/ECONOMY.md`](ECONOMY.md) — read that first; this doc assumes its
four phases (Salvage / Forge / Matcher / Ledger) and doesn't redefine
them. Where `ECONOMY.md` asks "what is this worth," this doc asks "how
much carbon does building it actually keep out of the atmosphere, and
for how long" — a different question, computed across the same four
phases, not a bolt-on metric to the first question.

## The twin mission

Salvage Forge exists for two co-equal reasons, not one: a sovereign
materials commons, and a carbon-capture engine that works by diverting
material from waste into durable construction. `ECONOMY.md` is the
materials-commons half — pricing, credit, the ledger. This document is
the carbon half. Neither is subordinate to the other. A materials
economy that doesn't capture carbon is just a barter system with extra
steps; a carbon program that can't sustain a functioning materials
economy has no supply of diverted material to capture carbon *from*.
The two missions need each other structurally, not just rhetorically —
which is exactly why this doc reuses `ECONOMY.md`'s four phases instead
of inventing a parallel carbon-tracking pipeline.

## The core mechanic: carbon = content × duration

Two independent measurements, each owned by the phase already
responsible for that kind of judgment in `ECONOMY.md`, multiplied at
the same intersection `ECONOMY.md` computes value at.

### Content — measured by Salvage

How much carbon is physically bound up in an item. Estimable from what
the passport already captures: `category`/`family` (the join key into a
coefficient table), and the dimensions intake already estimates
(`length_in`/`width_in`/`qty`). The chain is:

```
family --(coefficient table)--> mass estimate --(coefficient table)--> kg CO2e
```

**The coefficient table is the artifact this needs, and it doesn't
exist yet.** It should be keyed by `family` and reference a published
database — [ICE (Inventory of Carbon & Energy)](https://circularecology.com/embodied-carbon-footprint-database.html)
and [EC3](https://www.buildingtransparency.org/) are the standing
candidates — not hand-guessed or hardcoded per family as new items show
up. This document does not propose specific coefficient values; that's
a research task, not a design decision.

**Two flavors, and conflating them is a greenwashing trap:**

- **Sequestered carbon (biogenic)** — physically locked in the
  material itself: the cellulose in wood, cardboard, paper fiber. This
  carbon was pulled out of the atmosphere by a plant and stays bound as
  long as the material isn't burned or fully decomposed.
- **Avoided / embodied carbon** — emissions *dodged* by not
  manufacturing a virgin replacement (smelting new steel, firing new
  brick, extruding new plastic). Nothing is physically sequestered;
  the claim is counterfactual — "if this hadn't been reused, X kg CO2e
  would have been emitted making a new one instead."

These are different claims requiring different rigor. Sequestered
carbon is a physical fact about the material, verifiable by weighing
and identifying it. Avoided carbon is a counterfactual claim about what
*would have* happened, which is inherently harder to substantiate (see
additionality, below) and easier to overstate. A passport's carbon
estimate must say which flavor it's reporting — never a single
undifferentiated "CO2 saved" number.

### Duration — determined by Forge

How long the captured carbon actually stays captured. This is not a
property of the material — it's a property of *what gets built from
it*, which is exactly the "Forge defines the value" insight `ECONOMY.md`
already established for credit, applied here to time instead of price:
the same cardboard box is worth radically different capture-duration
depending on its fate. Mulched into garden beds, it releases its carbon
back within months. Built into a pallet shed's sheathing, it holds for
decades. A structural application built to last longer holds longer
still. Forge's templates are exactly the thing that decides an item's
fate — so Forge is where duration has to be estimated, the same way
Forge (via the Matcher) is where `ECONOMY.md` says price gets set, not
Salvage.

### Carbon potential = content × duration, computed as a range

Not a single number — a **range**. Templates are already parametric,
which is exactly what makes this so: `LinearDemand` doesn't ask for one
specific stick, it accepts any stock piece from a list of qualifying
`families`; `dome_3v(radius_in)` self-sizes to whatever radius the pile
actually supports. That parametricity is what makes per-slot material
*substitution* possible — and substitution is what turns a single fixed
carbon number into a range. If a template's "corner post" slot can be
filled by pallet lumber or dimensional framing, and those two families
carry different carbon coefficients, the same blueprint captures a
different amount of carbon depending on which one actually goes in.

The live computation, mechanically: a blueprint's parts list (its
demand slots) × the actual candidate pool of stock (earmarked or
otherwise selectable) that could fill each slot, evaluated at the
Matcher the same way `match_template` already evaluates feasibility.
Worst-case fill (lowest-carbon qualifying material in every slot) to
best-case fill (highest-carbon qualifying material in every slot)
brackets the range.

This range is worth advertising as a *feature* of the specific object
being built, not buried as an implementation detail: **"this dome:
X–Y kg CO2e realizable, depending on which materials fill it."** It's
the same parametric-by-design property that already lets
`best_dome_radius` say "the largest dome this pile supports" —
applied to carbon instead of size.

**Flag, not a design: carbon as a matcher optimization axis.** Today
`match_template`'s objective minimizes shortfall, then piece count
(`min 10_000 * Σs[k] + Σy[i]`) — it has no notion of which qualifying
piece is "better" to select when several would satisfy a slot equally
well. A natural future extension is optimizing slot-fill *for* carbon
content, not just feasibility — deliberately pushing a build toward the
high end of its range rather than accepting an arbitrary feasible fill.
That's real future work, plausible once stages A/B below exist, but it
is **not** designed in this document — it changes the MILP's objective
function, which deserves its own design pass, not a paragraph here.

**Coupling to credit value is deliberately undecided.** Two live
options — a carbon score reported in parallel to credit (its own
number, never converted), or carbon feeding into credit value itself
(a build with better capture duration draws more credit) — and this doc
does not choose between them. That choice has real consequences
(it changes what a credit *means*) and belongs in a future design
decision once the mechanics above actually produce numbers worth
arguing about. This document captures the mechanics; the coupling is
out of scope on purpose.

### Three layers, three owners

| Layer | Owned by | Persistence |
|---|---|---|
| **Content** | Salvage | Persisted — passport-level, per item (stage A) |
| **Duration** | Forge | Persisted — template-level, per blueprint (stage B) |
| **Carbon potential** | Matcher | Live, parametric range — recomputed against actual current inventory each time a template is evaluated; never persisted |

## Treatments: Forge's second capability

Forge holds more than blueprints. A blueprint is material -> object
(a template consuming stock to produce a build). A **treatment** is
material -> *better* material: a process that changes an item's carbon
profile without building anything from it yet. Two kinds:

- **Capture-increasing.** Raises content. Filling plastic bottles with
  carbon-rich filler to make infill blocks (ecobricks); shredding
  cardboard and locking the cellulose into a cement or lime matrix
  (papercrete). The treated item holds more carbon than the raw
  material did.
- **Preservation.** Extends duration. Sealing, borate or lime
  treatment, moisture control — none of these add carbon, but they
  extend how long the material survives inside a build before it
  degrades and releases what it's holding.

Mapped onto the three layers above: capture-increasing treatments
modify **persisted content** — a treated item genuinely has a new mass
and/or coefficient, a Salvage-side property change (its passport
content estimate needs recomputing after treatment, the same as any
other material fact would). Preservation treatments extend **persisted
duration** — a Forge-side property, since duration is already defined
as "how long a build holds," and preservation is exactly what stretches
that number for a given build. And the Matcher's live carbon-potential
range (above) should account for available treatments: a build's
best-case ceiling may assume treating candidate materials first, not
just selecting the best already-available piece as-is.

**The strategic point, stated plainly: treatments give carbon-worth to
exactly the materials the build-economy alone can't value.** A plastic
bottle that no current template demands as a build material is
worthless to `ECONOMY.md`'s value loop — no shortfall entry will ever
match it, so stage 2's demand signal never lights up for it. But it can
still be carbon-worth *something* once a treatment exists that turns it
into infill. The two missions cover each other's blind spots: the
materials economy prices what Forge can build with directly; the carbon
mission, through treatments, can still find worth in what Forge can't
build with *yet*, by changing the material rather than waiting for a
template that wants it as-is.

Nothing here is designed to code level — `matcher.jl` has no
material-to-material capability today (see "Relationship to existing
code," below); this section names the capability class, not an
implementation.

## Honest hard problems

Carbon is the area most exposed to greenwashing criticism of anything
in this project. Naming these prominently, not burying them, is what
keeps the whole effort credible:

- **Estimation vs. verification.** Everything above produces an
  *estimate* — useful for internal accounting and as an incentive
  signal inside the commons (e.g. surfacing which builds capture the
  most, the way `ECONOMY.md`'s demand signal surfaces which items are
  most needed). Turning an estimate into a sellable or subsidizable
  carbon credit requires third-party verification, audit, and
  certification against a real standard — none of which exists yet, or
  is designed here. Internal-incentive-grade and market-grade are not
  the same bar, and this document only claims the first.
- **Additionality — the carbon-market killer question.** Would this
  material have been landfilled (or burned, or left to rot) anyway? A
  diversion credit can only be legitimately claimed for material that
  would *not* otherwise have avoided that fate. A donor who was already
  planning to reuse their own pallets isn't creating additional capture
  by routing them through this system first. This is the single
  question that sinks the most carbon-offset claims industry-wide, and
  nothing in the content×duration mechanic above answers it — it has to
  be answered separately, per claim, before any external claim is made.
- **Permanence risk.** Duration is a *projection* at the time a build
  is completed, not a guarantee. If a pallet shed estimated at 20 years
  is demolished in 3, the carbon it was holding is released early — the
  estimate was wrong in a way that only becomes visible later, and by
  default nothing in this system would ever find out. Honest accounting
  states projected duration as exactly that, a projection, and doesn't
  quietly treat it as a locked-in fact once computed.
- **The oracle problem, again.** `ECONOMY.md`'s Ledger section names
  this for credit; it applies identically here. The intake AI attests
  to what material is present and estimates its mass — nothing
  independently verifies either claim. Fine for an internal incentive
  signal inside a trusted commons; not fine as the sole basis for a
  regulator's subsidy check or a buyer's purchased offset. The gap
  between those two bars is exactly what the verification layer
  (stage C, below) exists to close, and until it does, this system's
  carbon numbers should be treated as internal-grade only.

## Org form & funding — a named track, not a decision

Because carbon capture is a co-equal mission rather than a marketing
angle, the organization's legal form and funding strategy follow from
*it*, not just from the materials-economy side. This connects two
threads that were previously separate:

- `ECONOMY.md`'s "real money" hard problem already flags that cash
  buy-in for credits raises real questions — who holds the money, what
  legal form the operator takes, whether a purchased credit is a
  stored-value instrument. A **nonprofit or cooperative structure** is
  a well-understood, well-precedented answer to exactly that class of
  question.
- It's also the structure that most carbon grants, removal subsidies,
  tax credits (45Q-style mechanisms are one example of the *category*
  of instrument that exists — not a claim that this project qualifies
  for 45Q specifically, or a commitment to pursue it), and voluntary
  carbon markets expect a counterparty to have.

Carbon-as-mission and nonprofit-as-form reinforce each other: the form
that makes credit buy-in legitimate is close to the same form carbon
funders expect to see. But **the measurement rigor described above is
the precondition for any of that funding being legitimately
accessible** — a nonprofit wrapper around unverified estimates doesn't
solve additionality or the oracle problem, it just puts a more credible
name on the same open questions. This is a direction to investigate
with qualified legal and accounting help, not a decision this document
makes. Nothing here commits the project to a specific entity type,
timeline, or funding source.

## Relationship to existing code

| What | Exists today | Missing for carbon |
|---|---|---|
| Material identity | `category`/`family` on every passport (`intake.py`) and every `StockPiece` (`matcher.jl`) — the exact join key a coefficient table would key off of. | The coefficient table itself: `family` -> (mass estimate basis, kg CO2e/kg, sequestered-vs-avoided flavor), sourced from ICE/EC3 or similar. No ingestion of a published database exists. |
| Quantity | `length_in`/`width_in`/`qty` on the passport; `StockPiece.length`/`.width` in the matcher (the same repurposed-per-category convention `BulkDemand` already relies on for bulk quantities). | A usable **mass**. Dimensions give an area (sheet) or a length (linear), not a volume — no thickness/cross-section field exists, and `part` items often carry no dimensions at all (a wheel, a hinge). Practical fix, not a new intake field: key the coefficient table to mass-per-existing-dimension (kg per linear inch for `linear`, kg per sq in for `sheet`, kg per unit for `part`/`bulk`) rather than requiring geometry intake doesn't capture. Revisit if/when a real thickness field earns its keep. |
| Build definition | `Template` (`matcher.jl`): `name`, `note`, and four demand-type vectors (`linear`/`sheets`/`parts`/`bulk`). | A service-life field. Nothing on `Template` today says how long a build is expected to last — the exact input duration needs, and the natural place to add it (one new scalar field, consistent with how the struct is already shaped). |
| Piece-to-build tracing | `match_template`'s `plan` and `consume()` already know exactly which physical stock pieces feed which specific build — that pairing already exists and is thrown away once `cutsheets.txt` is written. | Nothing carries that consumption record forward into a content×duration computation. The data to compute carbon potential mechanically already flows through the matcher; it's just not captured anywhere past the printed cut sheet. |
| Slot substitution (the range) | `LinearDemand.families` already accepts multiple qualifying families per slot, and `dome_3v(radius_in)`/`best_dome_radius` already self-size against whatever the pile supports — the parametricity a carbon range depends on already exists structurally. | The range computation itself: evaluating a slot's full qualifying candidate pool for content, not just the one piece `match_template` happens to assign. `match_template` currently returns one feasible assignment, not the space of possible ones. |
| Treatments | Nothing — `matcher.jl` has only material -> object (`Template`), no material -> material capability. | A new capability class, structurally distinct from `Template`: something like a `Treatment` type (input family/category, output family/category or modified coefficient, and which layer it affects — content or duration). Doesn't extend an existing pattern the way `BulkDemand` did; it's a genuinely new kind of thing for Forge to hold. |

## Proposed staged approach

Same discipline as `ECONOMY.md`: each stage independently shippable,
leaves the system working on its own.

- **Stage A — per-item content estimate.** A coefficient table
  (`family` -> mass basis + kg CO2e/kg + flavor) and the arithmetic to
  turn a passport's existing fields into a content estimate. Unlike
  `ECONOMY.md`'s demand signal (deliberately never persisted, because
  it's a live market snapshot), carbon content is closer to a stable
  material property — more like the passport's existing `structural`/
  `thermal` fields than like `value_tier`'s demand flag. It's a
  reasonable candidate for an actual new **estimated-band** passport
  field (`CLAUDE.md` contract #3), not a display-only annotation.
  Ships independently of Forge or the Matcher; useful the moment it
  exists, even with no downstream consumer yet.
- **Stage B — per-build duration, and carbon potential.** A
  service-life field on `Template`, plus the live range computation
  itself: a blueprint's demand slots evaluated against the actual
  qualifying candidate stock for each slot (worst-case to best-case
  fill), using `match_template`'s existing feasibility-checking
  machinery rather than a new one. Depends on stage A existing to have
  per-candidate content numbers to range over; ships independently of
  the verification layer below. Optimizing slot-fill *for* carbon
  (rather than just reporting the range) is explicitly out of scope
  for this stage — see the flag in "the core mechanic," above.
- **Stage C — verification and additionality.** The gate before any
  number from stages A/B is used *externally* — for a sellable credit,
  a subsidy application, or a public claim. Third-party verification,
  an additionality test per claim, and permanence tracking against
  actual build lifespan (not just projected). This is process and
  governance as much as code, the same way `ECONOMY.md`'s "real money"
  hard problem is a named flag rather than a solved mechanism — stages
  A and B are legitimate to build and use internally well before stage
  C exists; stage C is what makes a claim legitimate to make to anyone
  outside the commons.
- **Stage D — treatments.** A `Treatment` capability class in
  `matcher.jl`, and the passport-side recomputation a capture-increasing
  treatment implies for content. Depends on stages A and B existing (a
  treatment needs a content and/or duration number to modify) but is
  otherwise independent of stage C — treatments are useful internally
  the moment they exist, the same as A and B.
