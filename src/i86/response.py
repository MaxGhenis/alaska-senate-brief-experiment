"""The response template forecasters fill in, and the checks a filled response must pass.

A forecaster returns one JSON object. `template(graph)` lists every number it must supply, as nulls;
`parse(graph, obj)` checks a filled copy and returns it in a normalized form for the simulator, or
raises `ResponseError` listing every problem found.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from .graph import Graph, Node

PMF_TOLERANCE = 0.02


class ResponseError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s):\n" + "\n".join(f"- {p}" for p in problems))
        self.problems = problems


def quantile_key(q: float) -> str:
    return f"p{round(q * 100):02d}"


def share_key(x: float) -> str:
    return f"le_{x * 100:g}"


def value_template(graph: Graph, node: Node) -> dict:
    if node.kind == "binary":
        return {"p": None}
    if node.kind == "quantity":
        return {quantile_key(q): None for q in graph.quantiles}
    if node.kind == "pmf":
        return {label: None for label in node.labels}
    if node.kind == "categorical":
        return {option: None for option in node.options}
    if node.kind == "share_cdf":
        return {share_key(x): None for x in graph.share_grid}
    raise ValueError(node.kind)


def template(graph: Graph) -> dict:
    nodes = {}
    for node in graph.forecast_nodes():
        item: dict = {}
        if node.baseline:
            item["baseline"] = {"value": None, "as_of": None, "source": None}
        item["forecasts"] = {e.key: value_template(graph, node) for e in graph.entries(node)}
        item["reasoning"] = None
        item["sources"] = []
        nodes[node.id] = item
    return {
        "forecaster": {"model": None, "prior_context": None, "notes": None},
        "research_summary": None,
        "nodes": nodes,
    }


@dataclass
class Forecasts:
    """A checked response. `values[node][entry_key]` is a float (binary), a list of floats in
    quantile, label, option or grid order (quantity, pmf, categorical, share_cdf)."""

    values: dict[str, dict[str, object]]
    baselines: dict[str, dict] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)
    sources: dict[str, list[str]] = field(default_factory=dict)
    forecaster: dict = field(default_factory=dict)
    research_summary: str = ""


def _number(x) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)) and math.isfinite(x):
        return float(x)
    if isinstance(x, str):
        try:
            v = float(x.replace(",", ""))
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None


def parse(graph: Graph, obj: dict) -> Forecasts:
    problems: list[str] = []
    if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), dict):
        raise ResponseError(["the response must be an object with a 'nodes' object"])
    out = Forecasts(values={})
    out.forecaster = obj.get("forecaster") or {}
    prior = out.forecaster.get("prior_context") if isinstance(out.forecaster, dict) else None
    if not isinstance(prior, str) or not prior.strip():
        problems.append("forecaster.prior_context: required; quote anything beyond the brief, or write 'none'")
    out.research_summary = obj.get("research_summary") or ""
    given_nodes = obj["nodes"]
    for node in graph.forecast_nodes():
        item = given_nodes.get(node.id)
        if not isinstance(item, dict):
            problems.append(f"{node.id}: missing")
            continue
        forecasts = item.get("forecasts")
        if not isinstance(forecasts, dict):
            problems.append(f"{node.id}: 'forecasts' missing")
            continue
        vals: dict[str, object] = {}
        for entry in graph.entries(node):
            where = f"{node.id}[{entry.key}]"
            v = forecasts.get(entry.key)
            if not isinstance(v, dict):
                problems.append(f"{where}: missing")
                continue
            checked = _check_value(graph, node, v, where, problems)
            if checked is not None:
                vals[entry.key] = checked
        extra = set(forecasts) - {e.key for e in graph.entries(node)}
        if extra:
            problems.append(f"{node.id}: unexpected forecast keys {sorted(extra)}")
        out.values[node.id] = vals
        if node.baseline:
            out.baselines[node.id] = item.get("baseline") or {}
        out.reasoning[node.id] = item.get("reasoning") or ""
        out.sources[node.id] = [s for s in (item.get("sources") or []) if isinstance(s, str)]
    unknown = set(given_nodes) - {n.id for n in graph.forecast_nodes()}
    if unknown:
        problems.append(f"unexpected nodes {sorted(unknown)}")
    if problems:
        raise ResponseError(problems)
    return out


def _check_value(graph: Graph, node: Node, v: dict, where: str, problems: list[str]):
    if node.kind == "binary":
        p = _number(v.get("p"))
        if p is None or not 0.0 <= p <= 1.0:
            problems.append(f"{where}: p must be a probability, got {v.get('p')!r}")
            return None
        return p
    if node.kind == "quantity":
        keys = [quantile_key(q) for q in graph.quantiles]
        xs = [_number(v.get(k)) for k in keys]
        if any(x is None for x in xs):
            problems.append(f"{where}: quantiles {keys} must all be numbers, got {v}")
            return None
        lo, hi = node.bounds
        if any(b < a for a, b in zip(xs, xs[1:])):
            problems.append(f"{where}: quantiles must not decrease, got {xs}")
            return None
        if xs[0] < lo or xs[-1] > hi:
            problems.append(f"{where}: quantiles must lie in [{lo}, {hi}], got {xs}")
            return None
        return xs
    if node.kind in {"pmf", "categorical"}:
        names = node.labels if node.kind == "pmf" else list(node.options)
        ps = [_number(v.get(name)) for name in names]
        if any(p is None or p < 0 for p in ps):
            problems.append(f"{where}: probabilities for {names} must be numbers >= 0, got {v}")
            return None
        total = sum(ps)
        if abs(total - 1.0) > PMF_TOLERANCE:
            problems.append(f"{where}: probabilities must sum to 1, got {total:.3f}")
            return None
        return [p / total for p in ps]
    if node.kind == "share_cdf":
        keys = [share_key(x) for x in graph.share_grid]
        fs = [_number(v.get(k)) for k in keys]
        if any(f is None or not 0.0 <= f <= 1.0 for f in fs):
            problems.append(f"{where}: cumulative probabilities {keys} must lie in [0, 1], got {v}")
            return None
        if any(b < a for a, b in zip(fs, fs[1:])):
            problems.append(f"{where}: cumulative probabilities must not decrease, got {fs}")
            return None
        return fs
    raise ValueError(node.kind)


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)

# The registered repairs. `read_answer` reads an answer as written first, and returns it unrepaired when
# `parse` accepts it. Only an answer that fails gets the repairs, the same in every arm, before `parse`
# checks it again; `read_answer` returns a record of each repair, which `i86 check` keeps in
# runs/<label>/repairs.json. Each mends a slip that carries no forecast, and nothing else:
# - a comma that follows an element and has only white space between it and a closing brace or
#   bracket, outside strings, goes, when the JSON parses only without it;
# - a key that the template lacks, inside a question's `forecasts` or among `nodes`, goes when its value
#   holds no object (null, text, a number, true or false, or a list with no object in it at any depth):
#   only an object can hold a forecast.
# A run that needs any other change still fails. `scripts/arms.py --strict` reruns the analysis without
# the repaired runs.


def _strip_trailing_commas(text: str) -> tuple[str, list[str]]:
    """`text` without each comma that, outside strings, follows an element (a string, a number, true,
    false, null, an object or a list) and has only white space between it and a closing brace or
    bracket, and the text just before each comma removed. A comma after `{`, `[`, `:` or another comma
    stays: it follows no element, and removing it would hide a hole rather than mend a slip."""
    out: list[str] = []
    removed: list[str] = []
    in_string = escaped = False
    comma: tuple[int, int] | None = None  # (index in out, index in text) of a comma not yet followed by anything
    prev = ""  # the last character outside white space, a string's closing quote included
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string, prev = False, '"'
        elif ch == '"':
            in_string, comma = True, None
        elif ch in "}]" and comma is not None:
            out[comma[0]] = ""
            removed.append(text[max(0, comma[1] - 40) : comma[1] + 1])
            comma, prev = None, ch
        elif ch == ",":
            comma = (len(out), i) if prev == '"' or prev in ("}", "]") or prev.isalnum() else None
            prev = ch
        elif not ch.isspace():
            comma, prev = None, ch
        out.append(ch)
    return "".join(out), removed


def _loads(text: str) -> tuple[object, list[dict]]:
    """json.loads(text), or, when only trailing commas stop it, the text parsed without them, with the
    repair's record. Raises the strict parse's JSONDecodeError otherwise."""
    try:
        return json.loads(text), []
    except json.JSONDecodeError as exc:
        fixed, removed = _strip_trailing_commas(text)
        if not removed:
            raise
        try:
            obj = json.loads(fixed)
        except json.JSONDecodeError:
            raise exc from None
        return obj, [{"repair": "trailing_comma", "count": len(removed), "before": removed}]


def extract_json(text: str, repairs: list | None = None, repair: bool = True, where: dict | None = None) -> dict:
    """The last fenced JSON block in an agent's final message that holds a 'nodes' object, else the
    outermost object. With `repair`, a block that parses only without its trailing commas is read
    without them, and the repair is appended to `repairs`; without it, every block is read as written.
    `where`, if given, gets `block`: which fenced block was read (1 for the first), or "outermost"."""
    def load(t: str) -> tuple[object, list[dict]]:
        return _loads(t) if repair else (json.loads(t), [])

    blocks = _FENCE.findall(text)
    for i in range(len(blocks) - 1, -1, -1):
        try:
            obj, fixes = load(blocks[i])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "nodes" in obj:
            if repairs is not None:
                repairs.extend(fixes)
            if where is not None:
                where["block"] = i + 1
            return obj
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ResponseError(["no JSON object found in the response"])
    try:
        obj, fixes = load(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ResponseError([f"the response's JSON does not parse: {exc}"]) from exc
    if repairs is not None:
        repairs.extend(fixes)
    if where is not None:
        where["block"] = "outermost"
    return obj


def expected_keys(graph: Graph) -> dict[str, set[str]]:
    """{question: the keys its `forecasts` takes}, as the template lists them."""
    return {n.id: {e.key for e in graph.entries(n)} for n in graph.forecast_nodes()}


def _holds_object(value) -> bool:
    """Whether a parsed JSON value is an object, or a list with an object in it at any depth."""
    if isinstance(value, dict):
        return True
    return isinstance(value, list) and any(_holds_object(v) for v in value)


def drop_strays(expected: dict[str, set[str]], obj) -> list[dict]:
    """Drop from a parsed answer, in place, each key the template lacks whose value holds no object
    (`_holds_object`), among `nodes` or inside a question's `forecasts` (`expected`, from
    `expected_keys`), and return a record of each key dropped."""
    fixes: list[dict] = []
    if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), dict):
        return fixes
    nodes = obj["nodes"]
    for key in list(nodes):
        if key not in expected and not _holds_object(nodes[key]):
            fixes.append({"repair": "dropped_key", "where": "nodes", "key": key, "value": nodes.pop(key)})
    for node_id, keys in expected.items():
        item = nodes.get(node_id)
        forecasts = item.get("forecasts") if isinstance(item, dict) else None
        if not isinstance(forecasts, dict):
            continue
        for key in list(forecasts):
            if key not in keys and not _holds_object(forecasts[key]):
                fixes.append({"repair": "dropped_key", "where": f"{node_id}.forecasts", "key": key,
                              "value": forecasts.pop(key)})
    return fixes


def read_answer(graph: Graph, text: str) -> tuple[dict, list[dict]]:
    """An agent's final message's JSON, and a record of each repair made. An answer that `parse` accepts
    as written comes back as written, with no repairs, so a repair can never change what such an answer
    says; only an answer that fails gets the registered repairs. `parse` still checks the result.

    The repaired reading picks its block afresh: the last fenced block that parses once its trailing
    commas go, which may come after the block the first reading took. Each repair record says which
    block the answer came from (`block`) and which the first reading took (`as_written_block`, None
    when no block or object parsed as written), so a reader sees when the two differ."""
    first: dict = {}
    try:
        obj = extract_json(text, repair=False, where=first)
        parse(graph, obj)
        return obj, []
    except Exception:  # any fault as written: the repaired reading below gets the same check
        pass
    repairs: list[dict] = []
    second: dict = {}
    obj = extract_json(text, repairs, where=second)
    repairs += drop_strays(expected_keys(graph), obj)
    for rec in repairs:
        rec.update(block=second.get("block"), as_written_block=first.get("block"))
    return obj, repairs
