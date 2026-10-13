# supply-chain-twin

[![ci](https://github.com/kavya2693/supply-chain-twin/actions/workflows/ci.yml/badge.svg)](https://github.com/kavya2693/supply-chain-twin/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Test replenishment, promotion and fleet-size changes on a digital twin of a retail chain
before they reach a single store, and accept only the ones that beat the current policy by
more than the twin's own error.

## The problem

A regional retail chain runs two distribution centres, 36 stores and a fleet of about 26
dispatchable trucks. Every few weeks someone proposes a change: order every two days
instead of daily so trucks leave fuller, give stores the promotion calendar so they stock
up before a drinks and snacks promotion, hand back a few leased trucks. Each idea sounds
cheaper on a spreadsheet. Trying one for real means a quarter of empty shelves or overflowing
backrooms before anyone knows whether it worked, and by then the cost is already paid.

The data to answer these questions exists, but it sits in three systems that were never
meant to be read together: the fleet management system knows the trucks, the merchandise
system knows items, costs, store locations and backroom sizes, and the order management
system knows what customers bought.

## Why it is hard

Building a simulation is not the hard part. Trusting its answer is. A single simulation run
per strategy turns demand noise into conclusions. A search that tries seventy variations
will find one that looks better by chance. And a strategy tuned for one quarter can quietly
depend on conditions (a full fleet, no breakdowns) that will not hold in the next one. The
twin itself is also wrong by some amount, and any "improvement" smaller than that error is
not an improvement anyone should roll out.

## The approach

The twin joins the three source tables with pandas into one state: per store-SKU demand
rates with weekday, monthly, holiday and promotion multipliers estimated from two years of
order lines, item economics, store-to-DC distances and backroom capacities, and the
dispatchable fleet per DC. Every repair made on the way in is counted. A **SimPy**
discrete-event model then plays out a quarter: stores sell in the evening, place
order-up-to orders, the DC dispatches loads in a 05:00-19:00 shift, and a truck that is out
on another trip makes the next store wait.

An evaluation harness runs each strategy over many seeds and compares it to the current
policy **on the same seeds**, so both face identical customers. A strategy agent proposes
one-step changes to the policy (safety factor, review frequency, cover days, fleet size,
promotion awareness); the harness accepts a step only if the whole 95% interval of the
cost change is below -0.5% of baseline cost and fill rate does not drop by more than 0.2
points, with the significance level split across the candidates of each round. An optional
local language model can replace the deterministic proposer; it can only suggest values on
the grid, and the harness still decides.

The key decision: **the floor of 0.5% is set by the twin's measured error, not by
statistics.** Paired seeds make confidence intervals so tight (about ±3,000 on a 2.4M
quarterly cost) that almost anything is significant; the backtest shows the twin's demand
is about 3% low, so gains smaller than a fraction of that are not believed.

## The result

All numbers below come from `make run` and are in [`outputs/`](outputs). Thirty paired
seeds per strategy, a 91-day horizon, costs in currency units per quarter.

**The plan the agent found is cheaper on the scenario it was tuned for and fails on the
scenario it was not.** Searching on the promotion quarter, the agent walked from the
current policy to *order every 2 days, safety factor 0.84, 8 fewer trucks, promotion-aware
ordering*. On 30 hold-out seeds it cuts total cost by 16.6% on that quarter. Re-tested on
the other two scenarios with the same seeds:

| Scenario (30 hold-out seeds) | Total cost change, 95% CI | Fill rate change | Truck utilisation | Verdict |
|---|---|---|---|---|
| Promotion quarter (tuned on) | **-407,200** (-410,363 to -404,038), -16.6% | -0.13 pts | 78% → 91% | holds |
| Normal quarter | -398,970 (-402,747 to -395,194) | **-0.21 pts** | 77% → 90% | fails the 0.2-pt fill limit |
| 20% of fleet out of service | **+63,322** (+25,451 to +101,193) | **-1.88 pts** | → 105% (overtime) | fails on both |

The search cut the fleet until the base fleet was just enough, so there is no slack left
for breakdowns: with a fifth of the trucks off the road, late loads rise to about 1,340 a
quarter and fill rate falls to 97.9%. The right reading is not "return 8 trucks" but
"ordering every two days is worth about 6% on its own; fleet cuts need a breakdown buffer
the search did not price."

Single changes against the current policy (promotion quarter, 30 seeds):

| Strategy | Total cost | vs current, ±95% | Fill rate | Promo fill rate | Truck util. | Load fill | Accepted |
|---|---|---|---|---|---|---|---|
| Current policy (daily, z=1.28, 1 day cover) | 2,460,080 | | 99.85% | 98.44% | 78.2% | 64.6% | |
| Order every 2 days | 2,318,072 | **-142,008** ±3,375 | 99.77% | 96.79% | 64.8% | 77.6% | yes |
| Fleet -4 trucks | 2,382,877 | -77,204 ±3,070 | 99.76% | 98.39% | 90.7% | 63.9% | yes |
| Promotion-aware ordering | 2,440,652 | -19,428 ±2,485 | 99.93% | **99.95%** | 78.3% | 64.6% | yes |
| Lean stock (z=0.84, no cover) | 2,588,004 | +127,924 ±5,004 | 99.22% | 93.72% | 78.4% | 64.6% | no |
| High service (z=2.05, 2 days cover) | 2,616,577 | +156,497 ±2,984 | 99.96% | 99.51% | 77.9% | 64.2% | no |
| Fleet +4 trucks | 2,547,522 | +87,442 ±2,691 | 99.87% | 98.45% | 68.5% | 65.3% | no |

Cost and service disagree in the places that matter. Ordering every two days is the
biggest single saving because trucks leave 78% full instead of 65%, but it is also the
change that hurts the promotion most (promo fill 98.4% → 96.8%), because a two-day cycle
reacts late to the uplift. Promotion-aware ordering is the reverse: a small cost gain and
almost perfect promotion availability. The high-service policy has the best fill rate in
the table and loses 156,000, most of it in backroom overflow (170,554 vs 16,939).

Twin fidelity, from a 13-week hold-out backtest (twin built on history that stops 91 days
early, scored on order lines it never saw):

| Weekly WAPE | Twin | 8-week mean | Same day last year |
|---|---|---|---|
| Store x SKU | **17.8%** | 24.5% | 29.1% |
| Store x category | **7.5%** | 18.1% | 19.4% |
| Store x SKU, promotion weeks | **18.2%** | 28.2% | 33.5% |
| Bias (total) | -3.3% | -9.3% | -12.0% |

The twin is better than both naive references at every level, but it under-forecasts by
3.3% because it does not model the year-on-year growth in the history. That bias is why the
acceptance floor exists.

## How it works

```
 FMS fleet ─┐                      ┌─ demand rates per store x SKU
 MMS items ─┼─ join + repair ─────►├─ weekday / month / holiday / promo multipliers
 MMS stores ┤  (data_quality.json) ├─ costs, case packs, distances, backrooms
 OMS orders ┘                      └─ dispatchable trucks per DC
                                         │
                         backtest ◄──────┤  error spread (sigma 0.104) injected into simulated demand
                                         ▼
          strategy ──► SimPy quarter (stores, DC dispatch, trucks in shift) ──► cost, fill, utilisation
             ▲                                    │  same seeds for candidate and baseline
             │                                    ▼
      proposer (neighbour or LLM) ◄── harness: accept if cost CI < -0.5% and fill loss < 0.2 pts
                                                  │  stop when a round accepts nothing
                                                  ▼
                              hold-out seeds + every scenario ──► outputs/
```

Each simulated day: 05:00-19:00 trucks take yesterday's orders out one load at a time
(a load waits for a free truck; a truck does not leave outside the shift); 21:00 stores
sell against what is on the shelf and unmet demand is lost; 21:30 stores due for review
order up to `reorder point + cover days`, where the reorder point is planned demand over
lead time plus review period plus `z` standard deviations. Total cost is holding cost,
backroom overflow (1.0 per case-day over capacity), transport (per-km plus driver hours),
the fleet's daily fixed cost, and the margin lost on unmet demand.

The search stops when a round accepts no candidate. Here it ran 8 rounds and tested 71
candidates on 20 search seeds; the hold-out uses 30 different seeds.

| Module | Role |
|---|---|
| `twin.py` | Loads the four source files, repairs and counts defects, estimates the twin state |
| `fidelity.py` | Hold-out backtest against two naive references; measures the error spread |
| `sim.py` | SimPy model, strategies, scenarios, cost accounting |
| `harness.py` | Paired-seed comparison, confidence intervals, acceptance rule, run cache, process pool |
| `agent.py` | Parameter grid, neighbour proposer, search loop, hold-out confirmation |
| `llm.py` | Optional Ollama proposer with JSON parsing, grid snapping and fallback |
| `cli.py` | `supply-twin quality / fidelity / evaluate / search / all` |

## Running it

```bash
git clone https://github.com/kavya2693/supply-chain-twin && cd supply-chain-twin
uv sync
make run          # generates data/, then runs everything into outputs/ (about 3 minutes on 4 cores)
make check        # ruff, ruff format, mypy, pytest
```

Single steps: `uv run supply-twin evaluate --seeds 30`, or
`uv run supply-twin search --scenario fleet_outage`. To let a local model propose
candidates: `ollama pull llama3.2 && uv run supply-twin search --proposer llm`.

Docker: `docker build -t supply-chain-twin . && docker run --rm supply-chain-twin`.

## The data

The dataset is generated by a seeded script (`scripts/generate_data.py`, `make data`) and
sized to the production problem; the production data this mirrors is proprietary. It
writes four files that follow the shape of the three source systems:

| File | Source system | Rows | Defects put in on purpose |
|---|---|---|---|
| `fms_fleet.csv` | fleet management | 28 trucks, 2 types | missing capacity, trucks in maintenance or retired |
| `mms_items.csv` | merchandise, item master | 120 SKUs in 8 categories | missing landed cost |
| `mms_locations.csv` | merchandise, location master | 36 stores, 2 DCs | none |
| `oms_order_lines.csv.gz` | order management | 3,126,764 lines, 2 years daily | bulk-buy outliers, store feed outages |

Demand has a Friday-to-Sunday peak, category seasonality, 4% annual growth, public holidays
with stock-up days before them, about one two-week promotion per category per quarter with
uplift from 1.2x to 1.9x, and negative-binomial noise. The twin found and repaired 1 item
with no cost, 1 truck with no capacity, 2 non-dispatchable trucks, 160 missing store-days
and 2,735 bulk-order cells (`outputs/data_quality.json`). Its estimated promotion uplifts
land within 0.06 of the generator's true values. The obvious limit: the twin is estimated
from data produced by the same family of assumptions it then simulates, so fidelity here is
an upper bound on what a real extract would give.

## Tests

25 tests, about 10 seconds, no model server. The two that matter most:

- `test_harness.py::test_acceptance_rule_on_known_cases` feeds the rule hand-built
  comparisons: a clear win, an interval that crosses zero, a real gain under the 0.5%
  floor, a cheaper plan that empties shelves, and a fill loss just inside the margin. The
  rule was written and checked against these before the search existed. A companion test
  runs a deliberately broken policy (z=0.5, no cover, 6 trucks fewer) through the real
  simulator and checks it is rejected.
- `test_sim.py::test_strategies_face_identical_customers` checks that two different
  strategies on the same seed see exactly the same demand. The paired comparison, and so
  every interval in this README, depends on it.

Others check that a feed outage is read as missing rather than zero sales, that a short
fleet shows up as late loads and lost sales, that promotion-aware ordering protects promo
fill, that LLM replies are snapped onto the grid, and that garbage or a dead server falls
back to the neighbour proposer.

## Known limitations

- **Biggest gap: the cost of fleet risk is not in the objective.** The search optimises
  expected cost for one scenario at a time, so it removes trucks until the base case is
  just covered and has nothing left for breakdowns, which the robustness table shows. A
  fix is to search against a weighted mix of scenarios or a worst-case constraint; neither
  is implemented.
- The language-model proposer is tested only with stubbed replies. A live run against
  `llama3.2` on this machine timed out (180 s per call) because the CPU was shared with
  other jobs, so there is no measured comparison between the model and the neighbour
  search.
- DCs have unlimited stock. Vendor lead times, DC labour and dock doors are not modelled,
  so DC-side effects of ordering every two days are missing.
- Sales are settled once per day at 21:00 and a load carries a proportional share of every
  SKU in the order; there is no intra-day timing of sales and no load building by SKU.
- Truck utilisation counts trip hours against a 14-hour shift and can exceed 100% when
  trucks run into overtime; overtime is charged at the normal driver rate.
- Cost constants (holding rate 25%, overflow 1.0 per case-day, driver 28 per hour) are
  stated, not estimated. Changing the overflow price changes which review period wins.
- Seed-to-seed variation is small because the network aggregates 4,320 store-SKUs, so the
  confidence intervals mostly measure demand noise, not structural uncertainty in the
  twin. The 0.5% floor is a judgement standing in for that.

## Insights and learning

I built this because "digital twin" usually means a dashboard that replays the past, and
the question a planner actually asks is about the future: if we change this, what breaks.
The useful part turned out not to be the simulation but the rules around it.

The first surprise was how tight paired confidence intervals are. With common random
numbers the cost difference between two strategies has an interval of a few thousand on a
2.4M base, so a Bonferroni correction changed nothing: every candidate the search tested
was either clearly better or clearly worse. I had expected multiple testing to be the
danger, and the real danger was believing tiny, perfectly significant gains from a twin
that is 3% wrong. That is where the 0.5% floor came from.

The second was the first version of the result. Before backroom capacity was in the
model, the search happily moved stores to ordering every three days, because nothing in the
cost function charged for stock that does not fit. Adding the backroom size from the
location master, and a price for overflow, moved the answer to two days. What a twin
leaves out decides its recommendation more than how carefully it simulates what it keeps.

The third was the robustness check, which I added expecting it to pass. A plan that saves
16.6% and fails the moment a fifth of the fleet is in the workshop is not a plan, and the
search had no way to know that because it only ever saw one scenario. Next I would search
over a mix of scenarios with the breakdown case weighted by how often it happens, measure
the language-model proposer against the neighbour search on candidates-to-convergence, and
add DC capacity so the cost of more frequent ordering is not only paid at the store.

## License

MIT
