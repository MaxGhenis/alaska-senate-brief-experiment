import copy
import json

import pytest

from i86 import graph as graph_mod
from i86.graph import Condition, GraphError, assignments, boolean_scope
from i86.prompt import build, sha256
from i86.response import ResponseError, extract_json, parse, quantile_key, template
from mock import I86_GRAPH, MOCK_response


def test_order_puts_parents_first(graph):
    position = {nid: i for i, nid in enumerate(graph.order)}
    for node in graph.nodes.values():
        for parent in node.parents:
            assert position[parent] < position[node.id], (parent, node.id)


def test_states_are_exclusive_and_exhaustive_wherever_the_node_exists(graph):
    # load() enforces this; re-check every node explicitly so a regression in load() is caught here.
    for node in graph.nodes.values():
        if not node.states:
            continue
        names = set().union(*(c.names for c in node.states.values()))
        if node.exists_if is not None:
            names |= node.exists_if.names
        for values in assignments(graph, boolean_scope(graph, names)):
            if node.exists_if is not None and not node.exists_if.evaluate(values):
                continue
            assert sum(c.evaluate(values) for c in node.states.values()) == 1, (node.id, values)


def test_overlapping_states_are_rejected(tmp_path):
    text = I86_GRAPH.read_text().replace(
        'certified: "ban_certified"', 'certified: "passes"'
    )
    path = tmp_path / "graph.yaml"
    path.write_text(text)
    with pytest.raises(GraphError, match="not exclusive and exhaustive"):
        graph_mod.load(path)


@pytest.mark.parametrize("bad", ["__import__('os')", "a + b", "x == 1", "f(x)", "x.y.z", "f(x).y"])
def test_conditions_accept_only_boolean_names(bad):
    with pytest.raises(GraphError):
        Condition.parse(bad)


def test_cycles_are_rejected(tmp_path):
    old = "    kind: categorical\n    module: implementation\n    exists_if: passes\n"
    text = I86_GRAPH.read_text()
    assert old in text
    text = text.replace(old, old.replace("exists_if: passes", "exists_if: passes and not pate_treated_banned"))
    path = tmp_path / "graph.yaml"
    path.write_text(text)
    with pytest.raises(GraphError, match="cycle"):
        graph_mod.load(path)


def test_mock_response_parses_and_covers_every_entry(graph):
    fc = parse(graph, MOCK_response(graph))
    for node in graph.forecast_nodes():
        assert set(fc.values[node.id]) == {e.key for e in graph.entries(node)}


def test_template_lists_every_entry_as_null(graph):
    t = template(graph)
    for node in graph.forecast_nodes():
        for entry in graph.entries(node):
            assert all(v is None for v in t["nodes"][node.id]["forecasts"][entry.key].values())


def _first_entry(obj, node_id):
    return next(iter(obj["nodes"][node_id]["forecasts"].values()))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda o: _first_entry(o, "lawsuit_filed").update(p=1.2), "probability"),
        (lambda o: _first_entry(o, "foie_gras_menus").update(p05=500), "not decrease|bounds|lie in"),
        (lambda o: _first_entry(o, "foie_gras_menus").update(p95=1), "not decrease"),
        (lambda o: _first_entry(o, "dem_share").update(le_90=0.1), "not decrease"),
        (lambda o: _first_entry(o, "paf_measures_2027_2028").update({"0": 5.0}), "sum to 1"),
        (lambda o: o["nodes"]["lawsuit_filed"]["forecasts"].clear(), "missing"),
        (lambda o: o["nodes"]["lawsuit_filed"]["forecasts"].update(extra={"p": 0.1}), "unexpected"),
        (lambda o: o["nodes"]["ban_status"]["forecasts"]["dem_60.0"].update(certified=5.0), "sum to 1"),
        (lambda o: o["nodes"].pop("turnout"), "turnout: missing"),
        (lambda o: _first_entry(o, "turnout").update(p50=None), "numbers"),
    ],
)
def test_parse_rejects_bad_values(graph, mutate, message):
    obj = MOCK_response(graph)
    mutate(obj)
    with pytest.raises(ResponseError, match=message):
        parse(graph, obj)


def test_numbers_given_as_strings_with_commas_are_read(graph):
    obj = MOCK_response(graph)
    entry = _first_entry(obj, "turnout")
    entry.update({quantile_key(q): f"{v:,.0f}" for q, v in zip(graph.quantiles, [150000, 180000, 200000, 220000, 260000])})
    fc = parse(graph, obj)
    assert fc.values["turnout"]["all"] == [150000, 180000, 200000, 220000, 260000]


def test_extract_json_takes_the_last_fenced_response(graph):
    obj = MOCK_response(graph)
    other = copy.deepcopy(obj)
    other["forecaster"]["model"] = "draft"
    text = f"notes\n```json\n{json.dumps(other)}\n```\nfinal:\n```json\n{json.dumps(obj)}\n```\n"
    assert extract_json(text)["forecaster"]["model"] == "MOCK"


def test_committed_prompts_match_the_graph(tx_graph):
    from i86.graph import ROOT
    from i86.prompt import ARMS

    recorded = dict(reversed(line.split("  arm-")) for line in
                    (ROOT / "prompts" / "SHA256SUMS").read_text().splitlines())
    for arm in ARMS:
        text = build(tx_graph, arm)
        assert (ROOT / "prompts" / f"arm-{arm}.md").read_text() == text, "run `i86 prompt`"
        assert recorded[f"{arm}.md"] == sha256(text)
    assert json.loads((ROOT / "schemas" / "template.json").read_text()) == template(tx_graph)


def _load_edited(tmp_path, old, new):
    text = I86_GRAPH.read_text()
    assert old in text
    path = tmp_path / "graph.yaml"
    path.write_text(text.replace(old, new))
    return graph_mod.load(path)


def test_option_tests_must_name_a_real_option_of_a_categorical_node(tmp_path):
    with pytest.raises(GraphError, match="unknown option"):
        _load_edited(tmp_path, 'rule: "ban_status.certified"', 'rule: "ban_status.in_force"')
    with pytest.raises(GraphError, match="not categorical"):
        _load_edited(tmp_path, 'rule: "ban_status.certified"', 'rule: "lawsuit_filed.certified"')
    with pytest.raises(GraphError, match="not boolean"):
        _load_edited(tmp_path, 'rule: "ban_status.certified"', 'rule: "ban_status"')


def test_pass_only_cells_need_an_exists_if_that_implies_passes(tmp_path):
    old = "    kind: categorical\n    module: implementation\n    exists_if: passes\n"
    with pytest.raises(GraphError, match="cells: pass"):
        _load_edited(tmp_path, old, old.replace("    exists_if: passes\n", ""))


def test_categorical_states_cover_every_option(graph):
    # foie_gras_menus splits the passing worlds by ban_status; every option lands in exactly one state.
    node = graph.nodes["foie_gras_menus"]
    for option in [None, *graph.nodes["ban_status"].options]:
        values = {"passes": option is not None, "ban_status": option,
                  "ban_standing": option in {"certified", "not_certified"}, "ban_certified": option == "certified"}
        assert sum(c.evaluate(values) for c in node.states.values()) == 1, option


def test_entries_are_only_the_pairs_that_can_occur(graph):
    keys = [e.key for e in graph.entries(graph.nodes["foie_gras_menus"])]
    assert keys == ["dem_49.5|failed", "dem_50.5|removed", "dem_50.5|not_certified", "dem_50.5|certified"]
    assert [e.key for e in graph.entries(graph.nodes["fattened_narrowed"])] == [
        "dem_50.5|certified", "dem_50.5|not_certified"]
    for node in graph.forecast_nodes():
        for e in graph.entries(node):
            if e.state == "failed":
                assert e.cell is not None and e.cell < 0.5, (node.id, e.key)


def test_prior_context_is_required(graph):
    obj = MOCK_response(graph)
    obj["forecaster"]["prior_context"] = None
    with pytest.raises(ResponseError, match="prior_context"):
        parse(graph, obj)


# --- the registered repairs (response.read_answer) -------------------------------------------------

from i86.response import read_answer  # noqa: E402


def _fenced(text: str) -> str:
    return f"Here is my answer.\n```json\n{text}\n```\n"


def test_mock_an_answer_that_parses_needs_no_repair(graph):
    obj = MOCK_response(graph)
    got, repairs = read_answer(graph, _fenced(json.dumps(obj, indent=1)))
    assert got == obj and repairs == []
    parse(graph, got)


def test_mock_trailing_commas_go_and_are_recorded(graph):
    """MOCK: commas before a closing brace and a closing bracket, as in the Texas study's opus-6."""
    obj = MOCK_response(graph)
    obj["nodes"]["turnout"]["sources"] = ["MOCK source"]
    text = json.dumps(obj, indent=1)
    text = text.replace('"MOCK source"\n', '"MOCK source",\n', 1).replace('"notes": null\n', '"notes": null,\n', 1)
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    got, repairs = read_answer(graph, _fenced(text))
    assert got == obj
    assert [r["repair"] for r in repairs] == ["trailing_comma"] and repairs[0]["count"] == 2
    parse(graph, got)


def test_mock_a_comma_inside_a_string_stays(graph):
    """MOCK: text such as ', }' inside a string is never touched, even when another comma goes."""
    obj = MOCK_response(graph)
    obj["nodes"]["turnout"]["reasoning"] = 'MOCK: odd text ", }" and ",]" here'
    text = json.dumps(obj, indent=1).replace('"notes": null\n', '"notes": null,\n', 1)
    got, repairs = read_answer(graph, _fenced(text))
    assert got["nodes"]["turnout"]["reasoning"] == 'MOCK: odd text ", }" and ",]" here'
    assert repairs[0]["count"] == 1


def test_mock_stray_keys_that_cannot_hold_a_forecast_go(graph):
    """MOCK: null, text, number and list values under keys the template lacks, as in the DC and Texas
    studies' '_note': null and 'dem_56.0_note_removed': null; an object under such a key stays and
    fails the check, since it may be a forecast the run meant to give."""
    obj = MOCK_response(graph)
    forecasts = obj["nodes"]["turnout"]["forecasts"]
    forecasts.update({"_note": None, "all_note_removed": "MOCK", "extra": 3, "more": [1]})
    obj["nodes"]["MOCK_unknown"] = None
    got, repairs = read_answer(graph, _fenced(json.dumps(obj)))
    assert {(r["where"], r["key"]) for r in repairs} == {
        ("turnout.forecasts", "_note"), ("turnout.forecasts", "all_note_removed"), ("turnout.forecasts", "extra"),
        ("turnout.forecasts", "more"), ("nodes", "MOCK_unknown")}
    assert got == MOCK_response(graph)
    parse(graph, got)
    forecasts["_note"] = {"p": 0.5}
    got, repairs = read_answer(graph, _fenced(json.dumps(obj)))
    with pytest.raises(ResponseError, match="unexpected forecast keys"):
        parse(graph, got)
    obj["nodes"]["MOCK_unknown"] = {"forecasts": {}}
    with pytest.raises(ResponseError, match="unexpected nodes"):
        parse(graph, read_answer(graph, _fenced(json.dumps(obj)))[0])


def test_mock_a_lost_closing_brace_still_fails(graph):
    """MOCK: a question whose forecasts object never closes, as in the DC study's opus-1, needs a
    guess about the answer's structure, so the repairs leave it failing."""
    obj = MOCK_response(graph)
    text = json.dumps(obj, indent=1)
    cut = text.index('"reasoning": "MOCK"')
    broken = text[:cut] + text[text.index('"turnout"', cut):]  # drops one question's closing and reasoning
    with pytest.raises(ResponseError):
        got, _ = read_answer(graph, _fenced(broken))
        parse(graph, got)


def test_mock_the_repairs_are_the_same_whatever_the_arm(tx_graph):
    """Invariant: read_answer sees only the answer's text, so two runs that wrote the same text get the
    same repairs, whatever arm either was in."""
    import inspect
    assert list(inspect.signature(read_answer).parameters) == ["graph", "text"]


@pytest.mark.parametrize("repo,run,repairable", [
    ("texas-governor-data-center-forecasts", "opus-2", True), ("texas-governor-data-center-forecasts", "opus-5", True),
    ("texas-governor-data-center-forecasts", "opus-6", True), ("texas-governor-data-center-forecasts", "opus-7", True),
    ("dc-i86-llm-forecast", "opus-1", False), ("dc-i86-llm-forecast", "opus-4", True),
    ("dc-i86-llm-forecast", "opus-6", True)])
def test_the_sister_studies_failed_checks(repo, run, repairable, tmp_path):
    """Differential on real answers: the seven that failed `i86 check` in the DC and Texas studies, read
    from their public repositories where they are checked out beside this one. After the repairs, with
    the keys each study's own template lists, six pass each study's own `parse`; DC's opus-1, which lost
    a question's closing braces, still fails."""
    import subprocess
    import sys
    from pathlib import Path

    from i86.response import drop_strays

    root = Path.home() / repo
    src = root / "runs" / run / "response.md"
    if not all(p.exists() for p in (src, root / "runs" / run / "problems.txt", root / "schemas" / "template.json")):
        pytest.skip(f"{repo} is not checked out at ~/{repo}")
    template_ = json.loads((root / "schemas" / "template.json").read_text())
    expected = {nid: set(item["forecasts"]) for nid, item in template_["nodes"].items()}
    repairs: list = []
    try:
        obj = extract_json(src.read_text(), repairs)
    except ResponseError:
        assert not repairable
        return
    repairs += drop_strays(expected, obj)
    out = tmp_path / "repaired.json"
    out.write_text(json.dumps(obj))
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from i86 import graph, response; "
            "assert response.__file__.startswith(sys.argv[1]); "
            "response.parse(graph.load(), json.loads(open(sys.argv[2]).read()))")
    proc = subprocess.run([sys.executable, "-c", code, str(root / "src"), str(out)], cwd=root,
                          capture_output=True, text=True)
    assert (proc.returncode == 0) == repairable, proc.stderr[-2000:]
    assert bool(repairs) == repairable or not repairable
