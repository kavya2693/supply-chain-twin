# ADR-0001: Paired seeds and a practical floor decide strategies; proposers only propose

## Status
Accepted

## Context
A digital twin is only useful if the decision it supports is one people can defend. The
twin here joins three operational sources (fleet, item and location masters, order lines)
and simulates a quarter of store ordering, DC dispatch and truck trips. Planners want to
ask it "should we order every two days", "do we need four more trucks", "should stores
order with the promotion calendar in hand", and they want a yes or no they can roll out.

Two failure modes were in view from the start. A single simulation run per strategy turns
demand noise into conclusions. And a search that tries many candidates, or a language
model that proposes them, will sooner or later "find" an improvement that is noise or a
modelling artefact.

## Decision
- **Discrete-event simulation in SimPy**, store-level processes with all SKUs of a store
  handled as one vector, trucks as tokens in a per-DC store with a shift window. A quarter
  of 36 stores x 120 SKUs runs in about 0.1-0.2 s, which is what makes many seeds per
  strategy affordable.
- **Common random numbers.** Demand (including the injected forecast error) depends only
  on the seed and scenario, never on the strategy. Candidate and baseline are compared as
  paired differences per seed.
- **Acceptance rule.** A candidate is accepted only if the whole confidence interval of
  its total-cost change (operating cost plus lost margin) lies below -0.5% of baseline
  cost, and the lower bound of its fill-rate change is above -0.2 points. Bonferroni
  splits alpha across the candidates of each search round.
- **Proposers only propose.** The deterministic neighbour search and the optional local
  LLM proposer both return candidates that are snapped onto a fixed parameter grid; only
  the harness moves the incumbent. Unparseable model output falls back to neighbours.
- **The searched plan is re-tested** against the starting policy on hold-out seeds and on
  every scenario, including ones it was not tuned on.
- **Forecast error in the simulation is measured, not chosen.** The spread of store-SKU
  error from a 13-week hold-out backtest of the twin is injected into simulated demand.

## Alternatives rejected
- **Independent seeds per strategy.** Run-to-run variation in total cost is several times
  larger than many of the effects being tested; pairing removes most of it.
- **Significance alone as the acceptance test.** With paired seeds, intervals are a few
  thousand on a 2.4M cost base, so 0.1% "wins" pass. The twin's demand bias in the
  backtest is about 3%, so gains that small are inside model error. The 0.5% floor is a
  judgement, recorded here so it can be argued with.
- **Letting the language model pick the policy.** Not reproducible and not checkable. The
  model is allowed to suggest; the harness is the judge.
- **PySpark for the joins.** The source tables are about three million order lines; pandas
  and numpy hold them in memory comfortably and keep CI free of a JVM.
- **AnyLogic / commercial twin tools.** Not open, not runnable in CI.

## Consequences
- The whole evaluation is reproducible from one command and needs no model server.
- Results are only as good as the cost model. Overflow, transport and lost-margin prices
  are stated constants in `sim.py`; changing them can change which strategy wins.
