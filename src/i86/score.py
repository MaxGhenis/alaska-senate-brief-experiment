"""The scoring rule every arm's brief states (rule 11), applied once outcomes are known.

Once the Senate result is certified, after any recount, the range it falls in is known. Each run's
forecast for that range is scored against what happened; its forecasts for the other seven ranges
are void.

- A yes/no question: the Brier score, (p - y)^2, with y = 1 if it happened.
- A quantity: the quantile score, the mean over the five quantiles q of the pinball loss
  max(q (y - x_q), (q - 1) (y - x_q)), in the question's unit.
- `dem_share`: the ranked probability score over the eight ranges, the sum over the seven range ends
  e of (F(e) - 1[y <= e])^2, where F(e) is the run's P(result <= e).

Lower is better for all three. If the Senate result is void (Peltola and Dan S. Sullivan are not the
last two candidates, or they tie), nothing is scored. A question whose own resolution is void, or
that has not resolved, is left out.

outcomes/outcomes.yaml records what happened, each entry with the date it resolved and its source:

    dem_share: {peltola: 104321, sullivan: 101234, as_of: 2026-11-25, source: <url>}
    recount: {value: false, as_of: 2026-12-31, source: <url>}
    bm2_yes_share: {value: 48.12, as_of: 2026-11-25, source: <url>}
    gov_dem_share: {void: "the Kreiss-Tomkins ticket was not in the last round", as_of: ..., source: ...}

`dem_share` takes the two candidates' votes in the last round; every other question takes its value
in its own unit (true or false for a yes/no question).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from .graph import Graph, Node, ROOT
from .response import Forecasts

OUTCOMES_PATH = ROOT / "outcomes" / "outcomes.yaml"
RULES = {"binary": "brier", "quantity": "quantile", "share_cdf": "rps"}


class OutcomeError(ValueError):
    pass


@dataclass(frozen=True)
class Outcome:
    value: float | bool | None  # None when void
    as_of: str
    source: str
    void: str = ""


def brier(p: float, happened: bool) -> float:
    return (float(p) - float(bool(happened))) ** 2


def pinball(x: float, q: float, y: float) -> float:
    d = y - x
    return max(q * d, (q - 1) * d)


def quantile_score(xs, qs, y: float) -> float:
    return sum(pinball(x, q, y) for x, q in zip(xs, qs, strict=True)) / len(qs)


def rps(cdf, edges, y: float) -> float:
    return sum((float(f) - float(y <= e)) ** 2 for f, e in zip(cdf, edges, strict=True))


def realized_cell(graph: Graph, share: float) -> float | None:
    """The range cell that holds `share` (a fraction), or None for a tie at exactly one half."""
    if share == 0.5:
        return None
    held = [c for c in graph.cells if (graph.ranges[c][0] is None or share > graph.ranges[c][0])
            and (graph.ranges[c][1] is None or share <= graph.ranges[c][1])]
    if len(held) != 1:
        raise OutcomeError(f"{len(held)} ranges hold a share of {share}")
    return held[0]


def load_outcomes(graph: Graph, path: Path = OUTCOMES_PATH) -> dict[str, Outcome]:
    raw = yaml.safe_load(path.read_text()) or {}
    nodes = {n.id: n for n in graph.forecast_nodes()}
    out = {}
    for qid, entry in raw.items():
        if qid not in nodes:
            raise OutcomeError(f"{qid}: not a question in the map")
        if not isinstance(entry, dict) or not entry.get("as_of") or not entry.get("source"):
            raise OutcomeError(f"{qid}: needs as_of and source")
        node = nodes[qid]
        if entry.get("void"):
            if set(entry) - {"void", "as_of", "source"}:
                raise OutcomeError(f"{qid}: a void outcome takes only void, as_of and source")
            out[qid] = Outcome(None, str(entry["as_of"]), str(entry["source"]), str(entry["void"]))
            continue
        if node.kind == "share_cdf":
            if set(entry) - {"peltola", "sullivan", "as_of", "source"}:
                raise OutcomeError(f"{qid}: takes peltola and sullivan votes, as_of and source")
            p, s = entry.get("peltola"), entry.get("sullivan")
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (p, s)) or p + s == 0:
                raise OutcomeError(f"{qid}: peltola and sullivan must be vote counts")
            value = p / (p + s)
        else:
            if set(entry) - {"value", "as_of", "source"}:
                raise OutcomeError(f"{qid}: takes value, as_of and source")
            value = entry.get("value")
            if node.kind == "binary" and not isinstance(value, bool):
                raise OutcomeError(f"{qid}: a yes/no outcome is true or false")
            if node.kind == "quantity" and (isinstance(value, bool) or not isinstance(value, (int, float))
                                            or not math.isfinite(value)):
                raise OutcomeError(f"{qid}: a quantity's outcome is a number")
        out[qid] = Outcome(value, str(entry["as_of"]), str(entry["source"]))
    return out


def node_score(graph: Graph, node: Node, forecast, value) -> float:
    """One forecast's score: `forecast` as `Forecasts.values` holds it, `value` as `load_outcomes` reads it."""
    if node.kind == "binary":
        return brier(forecast, value)
    if node.kind == "quantity":
        return quantile_score(forecast, graph.quantiles, float(value))
    if node.kind == "share_cdf":
        return rps(forecast, graph.share_grid, float(value))
    raise ValueError(f"{node.id}: no scoring rule for a {node.kind} question")


def score_run(graph: Graph, fc: Forecasts, outcomes: dict[str, Outcome]) -> list[dict]:
    """[{question, rule, range, score}] for each resolved question, scored at the realized range;
    empty while the Senate result is unknown or when it is void."""
    senate = outcomes.get("dem_share")
    if senate is None or senate.value is None:
        return []
    cell = realized_cell(graph, senate.value)
    if cell is None:
        return []
    label = graph.label(cell)
    rows = []
    for node in graph.forecast_nodes():
        o = outcomes.get(node.id)
        if o is None or o.value is None or not node.scored or node.kind not in RULES:
            continue
        keys = [e.key for e in graph.entries(node)]
        key = label if label in keys else keys[0] if len(keys) == 1 else None
        if key is None:
            continue  # not asked at the realized range
        rows.append({"question": node.id, "rule": RULES[node.kind], "range": label,
                     "score": node_score(graph, node, fc.values[node.id][key], o.value)})
    return rows
