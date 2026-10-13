from __future__ import annotations

import json

from supply_twin.agent import GRID, NeighbourProposer, search, snap
from supply_twin.harness import Evaluator
from supply_twin.llm import LLMProposer, build_prompt, parse_candidates
from supply_twin.sim import SCENARIOS, Strategy


def test_snap_forces_values_onto_grid() -> None:
    s = snap(Strategy(z=1.4, review_days=5, cover_days=0.7, extra_trucks=-13, promo_aware=True))
    assert s == Strategy(z=1.28, review_days=3, cover_days=0.5, extra_trucks=-10, promo_aware=True)


def test_neighbours_are_one_step_on_one_lever() -> None:
    inc = Strategy()
    for cand in NeighbourProposer().propose(inc, []):
        changed = [k for k in GRID if getattr(cand, k) != getattr(inc, k)]
        assert len(changed) == 1


def test_search_only_moves_on_accepted_steps_and_reports_holdout(twin) -> None:
    ev = Evaluator(twin, workers=1)
    res = search(ev, SCENARIOS["base"], NeighbourProposer(), [0, 1, 2], [100, 101, 102], max_rounds=2)
    for r in res.rounds:
        if r.accepted is not None:
            assert any(c.candidate == r.accepted and c.accepted for c in r.comparisons)
    if res.final != res.start:
        assert res.holdout is not None and res.holdout.n_seeds == 3


def test_llm_candidates_are_parsed_and_snapped() -> None:
    reply = json.dumps({"candidates": [{"z": 1.9, "review_days": 2, "extra_trucks": "-3"}], "reason": "x"})
    cands = parse_candidates(reply, Strategy())
    assert cands == [Strategy(z=2.05, review_days=2, cover_days=1.0, extra_trucks=-4)]


def test_llm_garbage_or_outage_falls_back_to_neighbours() -> None:
    def boom(prompt: str) -> str:
        raise ConnectionError("no server")

    for complete in (lambda p: "I think you should order more.", boom):
        prop = LLMProposer(complete)
        out = prop.propose(Strategy(), [])
        assert prop.fallbacks == 1
        assert out == NeighbourProposer().propose(Strategy(), [])


def test_llm_prompt_carries_grid_and_policy() -> None:
    prompt = build_prompt(Strategy(), [])
    assert '"review_days": [1, 2, 3]' in prompt
    assert '"z": 1.28' in prompt
