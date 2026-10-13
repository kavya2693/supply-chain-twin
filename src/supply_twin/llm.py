"""Optional language-model proposer, run against a local Ollama server.

The model only proposes; the harness still decides. Its output is parsed as JSON, every
value is snapped onto the parameter grid, and anything unparseable falls back to the
deterministic neighbour proposer, so a bad model reply costs one round of neighbours and
never an unchecked policy.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import fields

from supply_twin.agent import GRID, NeighbourProposer, snap
from supply_twin.harness import Comparison
from supply_twin.sim import Strategy

Complete = Callable[[str], str]


def ollama_complete(
    model: str = "llama3.2", host: str = "http://localhost:11434", timeout: float = 180
) -> Complete:
    def call(prompt: str) -> str:
        body = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(json.loads(resp.read())["response"])

    return call


def build_prompt(incumbent: Strategy, history: Sequence[Comparison]) -> str:
    lines = [
        "You tune the replenishment and fleet policy of a retail chain's digital twin.",
        "Lower total cost (operating cost plus lost margin) wins, without losing fill rate.",
        f"Allowed values: {json.dumps(GRID)}",
        f"Current policy: {json.dumps(incumbent.as_dict())}",
        "Results so far (candidate vs the policy it was tested against, total cost change, fill change):",
    ]
    for c in history[-12:]:
        lines.append(
            f"- {c.candidate.key()} | cost {c.cost_diff[0]:+,.0f} | fill {100 * c.fill_diff[0]:+.2f} pts"
            f" | {'accepted' if c.accepted else 'rejected'}"
        )
    lines.append(
        'Reply with JSON only: {"candidates": [{"z": ..., "review_days": ..., "cover_days": ..., '
        '"extra_trucks": ..., "promo_aware": ...}], "reason": "..."} with 1 to 4 untested candidates.'
    )
    return "\n".join(lines)


def parse_candidates(text: str, incumbent: Strategy) -> list[Strategy]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    raw = payload.get("candidates", []) if isinstance(payload, dict) else []
    names = {f.name for f in fields(Strategy)}
    out: list[Strategy] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        params = incumbent.as_dict()
        for k, v in item.items():
            if k not in names:
                continue
            try:
                params[k] = (str(v).lower() in {"true", "1", "yes"}) if k == "promo_aware" else float(v)
            except (TypeError, ValueError):
                continue
        params["review_days"] = round(float(params["review_days"]))
        params["extra_trucks"] = round(float(params["extra_trucks"]))
        out.append(snap(Strategy(**params)))  # type: ignore[arg-type]
    return out


class LLMProposer:
    name = "llm"

    def __init__(self, complete: Complete, max_candidates: int = 4) -> None:
        self.complete = complete
        self.max_candidates = max_candidates
        self.fallback = NeighbourProposer()
        self.fallbacks = 0
        self.replies: list[str] = []

    def propose(self, incumbent: Strategy, history: Sequence[Comparison]) -> list[Strategy]:
        tried = {(c.baseline, c.candidate) for c in history}
        try:
            reply = self.complete(build_prompt(incumbent, history))
        except Exception:  # a model server that is down must not stop the search
            reply = ""
        self.replies.append(reply)
        cands = [
            c for c in parse_candidates(reply, incumbent) if c != incumbent and (incumbent, c) not in tried
        ]
        cands = list(dict.fromkeys(cands))[: self.max_candidates]
        if not cands:
            self.fallbacks += 1
            return self.fallback.propose(incumbent, history)
        return cands
