# =============================================================================
# SALVAGE MATCHER — a "reverse bill-of-materials" engine (prototype v0.1)
#
# Normal construction:   design -> bill of materials -> go buy it
# This engine inverts:   inventory of salvage -> what can we build? -> cut sheet
#
# Core idea:
#   * Each buildable product is a parametric TEMPLATE = a list of demands
#     (linear cuts, sheet areas, discrete parts) expressed as constraints,
#     not as a fixed shopping list.
#   * A MILP (mixed-integer linear program) assigns inventory to demands.
#     Linear stock (lumber, conduit) is a classic CUTTING STOCK problem.
#   * If infeasible, slack variables tell us the *minimal shortfall* —
#     i.e. exactly what the kiosk should ask donors for next.
#
# Run:  julia matcher.jl inventory.csv
# =============================================================================

using JuMP, HiGHS, Printf

const KERF = 0.125            # saw blade width, inches — every cut eats this
const SHEET_UTILIZATION = 0.70 # naive 2D nesting allowance (future: real nesting)

# ---------------------------------------------------------------------------
# 1. INVENTORY
# ---------------------------------------------------------------------------

struct StockPiece            # one physical stick/sheet/part in the warehouse
    id::String               # e.g. "S02#3" (third 2x4 in lot S02)
    category::Symbol         # :linear, :sheet, :part
    family::String           # framing, conduit, plywood, wheel_26, ...
    length::Float64          # inches (linear: length; sheet: long side)
    width::Float64           # inches (sheet only)
    condition::Char
end

"Parse the inventory CSV and explode qty lots into individual pieces."
function load_inventory(path::String)
    pieces = StockPiece[]
    for (i, line) in enumerate(eachline(path))
        i == 1 && continue                       # skip header
        isempty(strip(line)) && continue
        f = split(line, ',')
        qty = parse(Int, f[7])
        for n in 1:qty
            push!(pieces, StockPiece("$(f[1])#$n", Symbol(f[2]), String(f[3]),
                                     parse(Float64, f[5]), parse(Float64, f[6]),
                                     first(strip(f[8]))))
        end
    end
    return pieces
end

# ---------------------------------------------------------------------------
# 2. TEMPLATES — the "pre-conceived structures" catalog
# ---------------------------------------------------------------------------
# A demand says: "I need `qty` cuts of `length` from any family in `families`"
# (linear), or "this many square inches of sheet", or "these discrete parts".

struct LinearDemand;  name::String; length::Float64; qty::Int; families::Vector{String}; end
struct SheetDemand;   name::String; area::Float64;   families::Vector{String};           end
struct PartDemand;    name::String; qty::Int;        families::Vector{String};           end

struct Template
    name::String
    note::String
    linear::Vector{LinearDemand}
    sheets::Vector{SheetDemand}
    parts::Vector{PartDemand}
end

# --- Template 1: Geodesic dome, 3-frequency 5/8 sphere -----------------------
# Geometry straight from Domebook 2 (Pacific Domes, 1971): chord factors
# A=.3486 x30, B=.4035 x55, C=.4124 x80 struts. Strut length = factor * radius.
# The template is PARAMETRIC in radius — the caller searches for the largest
# radius the inventory can support. This is "the dome shrinks to fit the pile."
function dome_3v(radius_in::Float64)
    fam = ["framing"]        # 2x lumber only; mixing families = structural mess
    Template("Geodesic dome 3V 5/8 (r=$(round(radius_in/12, digits=1)) ft, " *
             "dia=$(round(radius_in/6, digits=1)) ft)",
             "Domebook 2 geometry. 165 struts, 61 hubs (hose-clamp or strap). " *
             "Garden/play/greenhouse duty at small radii.",
             [LinearDemand("A strut", 0.3486 * radius_in, 30, fam),
              LinearDemand("B strut", 0.4035 * radius_in, 55, fam),
              LinearDemand("C strut", 0.4124 * radius_in, 80, fam)],
             SheetDemand[], PartDemand[])
end

# --- Template 2: Cold frame from a reclaimed window ---------------------------
# Sized parametrically off whatever window is in inventory: the window IS the lid.
function cold_frame(win_l::Float64, win_w::Float64)
    Template("Cold frame ($(win_l)x$(win_w) window lid)",
             "Reclaimed window hinged onto a sloped box. Pallet lumber ok.",
             [LinearDemand("long rail",  win_l, 4, ["pallet", "framing"]),
              LinearDemand("short rail", win_w, 4, ["pallet", "framing"]),
              LinearDemand("corner post", 14.0, 2, ["pallet", "framing"]),
              LinearDemand("corner post short", 8.0, 2, ["pallet", "framing"])],
             SheetDemand[],
             [PartDemand("window lid", 1, ["window"]),
              PartDemand("hinges", 2, ["hinge"])])
end

# --- Template 3: Bike cargo trailer -------------------------------------------
# Shows all three demand types: matched discrete parts (2 same-size wheels),
# cutting stock (EMT frame), and sheet area (plywood deck).
function bike_trailer(wheel_family::String)
    Template("Bike cargo trailer (24x48 deck, $(wheel_family))",
             "EMT frame, plywood deck, salvaged wheel pair. Code-free build.",
             [LinearDemand("side rail",  60.0, 2, ["conduit"]),
              LinearDemand("crossbar",   24.0, 3, ["conduit"]),
              LinearDemand("hitch tongue", 40.0, 1, ["conduit"])],
             [SheetDemand("deck", 24.0 * 48.0, ["plywood", "osb"])],
             [PartDemand("wheel pair", 2, [wheel_family])])
end

# ---------------------------------------------------------------------------
# 3. THE MATCHING ENGINE (this is the crown jewel)
# ---------------------------------------------------------------------------
"""
Solve: can `inv` satisfy `tpl`?  Returns (feasible, cutplan, shortfall).

MILP formulation (the classic one-to-many cutting stock / assignment hybrid):
  x[i,k] ∈ Z+  : number of cuts of demand k taken from stock piece i
  y[i]   ∈ 0/1 : is piece i consumed at all
  s[k]   ∈ Z+  : shortfall slack — cuts of k we could NOT source

  min  BIG * Σ s[k]  +  Σ y[i]          (satisfy first, then use fewest pieces)
  s.t. Σ_k x[i,k] * (len_k + KERF) ≤ len_i * y[i]   ∀ pieces i
       Σ_i x[i,k] + s[k] = qty_k                     ∀ demands k

Sheet and part demands are handled by simpler capacity checks folded into
the same report (real 2D nesting is future work — see README).
"""
function match_template(inv::Vector{StockPiece}, tpl::Template)
    lin = tpl.linear
    stock = [p for p in inv if p.category == :linear]
    nI, nK = length(stock), length(lin)

    model = Model(HiGHS.Optimizer)
    set_silent(model)

    # compat[i,k]: piece i may serve demand k (family allowed & long enough)
    compat = [stock[i].family in lin[k].families &&
              stock[i].length >= lin[k].length + KERF for i in 1:nI, k in 1:nK]

    @variable(model, x[i=1:nI, k=1:nK; compat[i, k]] >= 0, Int)
    @variable(model, y[1:nI], Bin)
    @variable(model, s[1:nK] >= 0, Int)

    for i in 1:nI
        ks = [k for k in 1:nK if compat[i, k]]
        isempty(ks) && continue
        @constraint(model, sum(x[i, k] * (lin[k].length + KERF) for k in ks)
                           <= stock[i].length * y[i])
    end
    for k in 1:nK
        is = [i for i in 1:nI if compat[i, k]]
        @constraint(model, (isempty(is) ? 0 : sum(x[i, k] for i in is)) + s[k]
                           == lin[k].qty)
    end
    @objective(model, Min, 10_000 * sum(s) + sum(y))
    optimize!(model)

    shortfall = Dict{String,Int}()
    plan = Dict{String,Vector{Tuple{String,Int}}}()   # piece id => [(cut, n)...]
    for k in 1:nK
        v = round(Int, value(s[k]))
        v > 0 && (shortfall[lin[k].name * @sprintf(" @ %.1f\"", lin[k].length)] = v)
    end
    for i in 1:nI, k in 1:nK
        compat[i, k] || continue
        n = round(Int, value(x[i, k]))
        n > 0 && push!(get!(plan, stock[i].id, Tuple{String,Int}[]),
                       (@sprintf("%s @ %.1f\"", lin[k].name, lin[k].length), n))
    end

    # ---- sheet demands: area check with utilization factor ----
    sheet_ok = true
    for d in tpl.sheets
        avail = sum(p.length * p.width * SHEET_UTILIZATION
                    for p in inv if p.category == :sheet && p.family in d.families;
                    init = 0.0)
        avail < d.area && (sheet_ok = false;
                           shortfall["sheet: $(d.name)"] = 1)
    end
    # ---- part demands: count check ----
    for d in tpl.parts
        have = count(p -> p.family in d.families, inv)  # match by family, any category
        have < d.qty && (shortfall["part: $(d.name)"] = d.qty - have)
    end

    feasible = isempty(shortfall) && sheet_ok
    return feasible, plan, shortfall
end

"For the dome: search the largest radius the pile supports (coarse grid)."
function best_dome_radius(inv; lo=30.0, hi=120.0, step=2.0)
    best = nothing
    for r in lo:step:hi
        ok, plan, _ = match_template(inv, dome_3v(r))
        ok ? (best = (r, plan)) : break     # monotone: bigger r only gets harder
    end
    return best
end

"Remove consumed linear pieces + used parts/sheets from inventory (sequential planning)."
function consume(inv::Vector{StockPiece}, tpl::Template, plan)
    used_ids = Set(keys(plan))
    remaining = [p for p in inv if !(p.id in used_ids)]
    for d in tpl.parts                     # remove parts greedily
        n = d.qty
        remaining = filter(remaining) do p
            take = n > 0 && p.family in d.families
            take && (n -= 1)
            !take
        end
    end
    for d in tpl.sheets                    # remove whole sheets until area met
        need = d.area / SHEET_UTILIZATION
        remaining = filter(remaining) do p
            take = need > 0 && p.category == :sheet && p.family in d.families
            take && (need -= p.length * p.width)
            !take
        end
    end
    return remaining
end

# ---------------------------------------------------------------------------
# 4. REPORTING
# ---------------------------------------------------------------------------
function print_cutsheet(io, tpl::Template, plan, inv)
    println(io, "="^64, "\nCUT SHEET: ", tpl.name, "\n", tpl.note, "\n", "="^64)
    lookup = Dict(p.id => p for p in inv)
    waste_total, used = 0.0, 0
    for id in sort!(collect(keys(plan)))
        p = lookup[id]; used += 1
        cuts = plan[id]
        used_len = sum(n * (parse(Float64, match(r"([\d.]+)\"", c).captures[1]) + KERF)
                       for (c, n) in cuts)
        offcut = p.length - used_len
        waste_total += offcut
        println(io, @sprintf("%-8s %-28s %6.1f\" stick", id, p.family, p.length))
        for (c, n) in cuts
            println(io, "         cut $n x  $c")
        end
        println(io, @sprintf("         offcut: %.1f\"  %s", offcut,
                             offcut >= 14 ? "<- RETURN TO INVENTORY" : "(scrap)"))
    end
    println(io, "-"^64)
    println(io, @sprintf("Pieces consumed: %d   Total offcut: %.1f\" (%.1f ft)",
                         used, waste_total, waste_total / 12))
end

# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------
function main(path)
    inv = load_inventory(path)
    println("Loaded $(length(inv)) pieces from $path\n")
    println("WHAT CAN THIS PILE BECOME?  (independent feasibility)\n")

    # Build the catalog dynamically from what inventory suggests
    windows = [p for p in inv if p.family == "window"]
    catalog = Template[]
    !isempty(windows) && push!(catalog, cold_frame(windows[1].length, windows[1].width))
    for wf in ["wheel_26", "wheel_20"]
        count(p -> p.family == wf, inv) >= 0 && push!(catalog, bike_trailer(wf))
    end

    results = []
    for tpl in catalog
        ok, plan, short = match_template(inv, tpl)
        push!(results, (tpl, ok, plan, short))
        flag = ok ? "BUILDABLE " : "MISSING   "
        println("  [$flag] ", tpl.name)
        for (item, n) in short
            println("              needs $n more: $item   <- kiosk wish-list")
        end
    end
    dome = best_dome_radius(inv)
    if dome !== nothing
        r, plan = dome
        tpl = dome_3v(r)
        push!(results, (tpl, true, plan, Dict()))
        println("  [BUILDABLE ] ", tpl.name, "  (largest radius the pile supports)")
    else
        println("  [MISSING   ] Geodesic dome — not enough framing stock at any radius")
    end

    # Sequential plan: build everything buildable, consuming inventory in order
    println("\nSEQUENTIAL BUILD PLAN (consuming inventory):\n")
    pool = inv
    open("cutsheets.txt", "w") do io
        for (tpl, ok0, _, _) in results
            ok0 || continue
            ok, plan, short = match_template(pool, tpl)
            if ok
                println("  BUILD: ", tpl.name)
                print_cutsheet(io, tpl, plan, pool)
                pool = consume(pool, tpl, plan)
            else
                println("  SKIP (inventory exhausted): ", tpl.name)
            end
        end
    end
    println("\nRemaining inventory: $(length(pool)) pieces")
    println("Cut sheets written to cutsheets.txt")
end

main(length(ARGS) >= 1 ? ARGS[1] : "inventory.csv")
