"""Build the four forecaster briefs from the graph, the context files and the response template.

The briefs are the study's arms. They are the same, byte for byte, except for two blocks, each in or
out: the estimand block (the two sentences of DC's brief, adapted, that told its forecasters what the
cells beside 50% measure and that a larger win can imply stronger support), and the placebo block
(DC's sentences, adapted, that told them the same-day placebo should not jump at 50%). An arm's id
gives the blocks in that order: `10` has the estimand block and not the placebo block. Arm `11` asks
the way DC's brief asked, arm `00` the way Texas's did. Each run records its arm and its brief's
SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .graph import ROOT, Graph, Node
from .response import quantile_key, share_key, template

CONTEXT = ROOT / "context"

ARMS = ("00", "01", "10", "11")

# DC's rule 3 said: "Everything else about the world is whatever you think is consistent with that
# result, so a 72% win can imply stronger public support than a 50.5% win. The `yes_49.5` and
# `yes_50.5` cells differ almost only in whether the measure passed; the difference between them is
# your estimate of what a bare pass changes. Denver votes the same day, so DC's result cannot change
# Denver's: there the two cells should differ only by what one point of DC margin says."
ESTIMAND = (
    ", so a share above 51.5% can imply stronger support for Peltola than one just above 50%. The "
    "`dem_49.5_50.0` and `dem_50.0_50.5` ranges differ almost only in who won; the difference between "
    "them is your estimate of what a bare win changes."
)
PLACEBO_RULE = (
    " The same ballots decide Ballot Measure 2, so the Senate result cannot change its result: "
    "there the two ranges should differ only by what half a point of Peltola's share says about voters."
)
# DC's Denver question carried this note: "Forecast at each DC cell so the two results can be
# correlated. Denver votes the same day, so DC's result cannot cause Denver's: the 49.5% and 50.5%
# cells should differ only by what one point of DC margin says about voters."
PLACEBO_NODE = "bm2_yes_share"
PLACEBO_NOTE = (
    "Forecast at each range so the two results can be correlated. The same ballots decide both, so the "
    "Senate result cannot cause Measure 2's: the `dem_49.5_50.0` and `dem_50.0_50.5` ranges should "
    "differ only by what half a point of Peltola's share says about voters."
)

INSTRUCTIONS = """\
# Forecasting brief: Alaska's 2026 US Senate race and what follows from it

You are one of several forecasters working independently on the same questions. Your forecasts will be
logged before the Alaska Division of Elections' next count, on 2026-11-10. Forecast with what is known
as of {as_of}. Research the questions yourself with web search, reason from base rates and current
facts, and give your own numbers.

## Rules

1. Work from public sources and this brief. Vote counts, polls, prediction markets, court filings,
   campaign statements and published analyses are all fair game. Do not read local files, other than
   this brief, on the machine you run on.
2. Every number is an absolute level in the unit stated (percent, senators, barrels, probabilities),
   never a ratio or a change.
3. The Senate result is Mary Peltola's share of the votes for Peltola and Dan S. Sullivan in the last
   round of the certified ranked-choice tabulation, after any recount (`dem_share` below gives the
   details). Above 50% Peltola wins; below it Sullivan does.
4. Each forecast is conditional on its scenario. A *range* such as `dem_50.0_50.5` means: the Senate
   result is above 50.0% and at most 50.5%, so Peltola wins narrowly; `dem_49.5_50.0` means above 49.5%
   and below 50.0%, so Sullivan wins narrowly. Everything else about the world is whatever you think is
   consistent with that result{estimand}{placebo}
5. Every question but `dem_share` is asked at all eight ranges: {ranges}.
6. Quantities take five quantiles (p05, p25, p50, p75, p95): your 5th percentile, quartiles, median and
   95th percentile. Quantiles must not decrease and must lie within the stated bounds.
7. `dem_share` takes your probability that the Senate result is at or below each grid value (`le_50`
   is P(result <= 50%), your probability that Sullivan wins). These must not decrease.
8. Where a question has a `baseline`, report the current value you found with its date and source.
9. Keep each question's `reasoning` to a few sentences: the base rate, the main mechanism, and what
   moves the forecast across ranges. List the URLs you relied on in `sources`.
10. Before you research, record in `forecaster.prior_context` anything in your context other than this
    brief (system prompt, memory, instructions, earlier conversation) that mentions Alaska's 2026
    elections or forecasts of these questions. Quote it, or write "none".
11. Your forecasts are scored. Once the result is certified, after any recount, the range it falls in
    is known, and each question's forecast for that range is scored against what happens: a
    probability by its Brier score, a quantity by the quantile score of your five quantiles, and
    `dem_share` by its ranked probability score. Forecasts for the other seven ranges are void and are
    not scored.

## Output

Your final message must be the completed template below as a single JSON object in one ```json fenced
block, with every null replaced. Put your model name in `forecaster.model` and a short summary of your
research in `research_summary`. Use exactly the template's keys: add none and rename none. Nothing
after the block.
"""

QUESTIONS_INTRO = (
    "The Senate result sets the ranges. Every other question, on Ballot Measure 2 and the governor's "
    "race, on the count and the courts, on the Senate seat, and on Alaska's economy, is forecast at each "
    "range. Read every question before forecasting any, so your answers cohere.\n"
)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def ranges_text(graph: Graph) -> str:
    """Rule 5's list of the ranges, from the map, lowest first."""
    parts = []
    for c in graph.cells:
        lo, hi = graph.ranges[c]
        if lo is None:
            what = f"at or below {_pct(hi)}"
        elif hi is None:
            what = f"above {_pct(lo)}"
        elif hi == 0.5:
            what = f"above {_pct(lo)} and below {_pct(hi)}"
        else:
            what = f"above {_pct(lo)} to {_pct(hi)}"
        parts.append(f"{what} (`{graph.label(c)}`)")
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _entries_text(graph: Graph, node: Node) -> str:
    entries = graph.entries(node)
    if [e.key for e in entries] == ["all"]:
        return "one forecast (`all`)"
    if graph.ranges and [e.key for e in entries] == [graph.label(c) for c in graph.cells]:
        return "all eight ranges"
    return ", ".join(f"`{e.key}`" for e in entries)


def _states_text(node: Node) -> list[str]:
    return [f"  - `{name}`: {cond.text}" for name, cond in node.states.items()]


def node_text(graph: Graph, node: Node, note: str = "") -> str:
    lines = [f"### `{node.id}`: {node.question}", ""]
    if node.kind == "derived":
        lines.append(f"- Computed, not forecast: true when `{node.rule.text}`.")
        if node.notes:
            lines.append(f"- {node.notes}")
        return "\n".join(lines)
    if node.kind == "binary":
        kind = "probability"
    elif node.kind == "quantity":
        kind = (f"quantity in {node.unit}, bounds [{node.bounds[0]:g}, {node.bounds[1]:g}]"
                + (", a whole number" if node.integer else "")
                + "; give " + ", ".join(quantile_key(q) for q in graph.quantiles))
    elif node.kind == "pmf":
        kind = "probability for each of " + ", ".join(f"`{x}`" for x in node.labels)
    elif node.kind == "categorical":
        kind = "probability for each option (they are exclusive and sum to 1)"
    else:
        kind = "cumulative probabilities at " + ", ".join(f"`{share_key(x)}`" for x in graph.share_grid)
    lines.append(f"- Answer each with: {kind}.")
    lines.append(f"- Forecasts: {_entries_text(graph, node)}.")
    if node.states:
        lines.append("- States:")
        lines += _states_text(node)
    if node.options:
        lines.append("- Options:")
        lines += [f"  - `{option}`: {text}" for option, text in node.options.items()]
    if node.exists_if is not None:
        lines.append(f"- Defined only if `{node.exists_if.text}`; void otherwise.")
    lines.append(f"- Resolution: {node.resolution}")
    if node.baseline:
        lines.append("- Report a baseline (current value, date, source).")
    if not node.scored:
        lines.append("- Not scored: no source can settle it, so it is logged as an anchor.")
    for text in (node.notes, note):
        if text:
            lines.append(f"- Note: {text}")
    return "\n".join(lines)


def check_arm(arm: str) -> tuple[bool, bool]:
    """(estimand block, placebo block) for an arm id."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    return arm[0] == "1", arm[1] == "1"


def build(graph: Graph, arm: str) -> str:
    estimand, placebo = check_arm(arm)
    if placebo and PLACEBO_NODE not in graph.nodes:
        raise ValueError(f"the placebo block needs the node {PLACEBO_NODE}")
    sections = [INSTRUCTIONS.format(as_of=graph.as_of, ranges=ranges_text(graph),
                                    estimand=ESTIMAND if estimand else ".",
                                    placebo=PLACEBO_RULE if placebo else "")]
    sections.append("## The questions\n")
    sections.append(QUESTIONS_INTRO)
    for module, title in graph.modules.items():
        sections.append(f"## {title}\n")
        for nid in graph.order:
            node = graph.nodes[nid]
            if node.module == module:
                note = PLACEBO_NOTE if placebo and nid == PLACEBO_NODE else ""
                sections.append(node_text(graph, node, note) + "\n")
    sections.append("## Context\n")
    sections.append((CONTEXT / "brief.md").read_text())
    sections.append("## Response template\n")
    sections.append("```json\n" + json.dumps(template(graph), indent=1) + "\n```\n")
    return "\n".join(sections)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def path_for(directory: Path, arm: str) -> Path:
    check_arm(arm)
    return directory / f"arm-{arm}.md"


def write(graph: Graph, directory: Path) -> dict[str, str]:
    """Write every arm's brief into `directory`, with a SHA256SUMS file; return {arm: sha256}."""
    directory.mkdir(parents=True, exist_ok=True)
    shas = {}
    for arm in ARMS:
        text = build(graph, arm)
        path_for(directory, arm).write_text(text)
        shas[arm] = sha256(text)
    (directory / "SHA256SUMS").write_text("".join(f"{shas[a]}  arm-{a}.md\n" for a in ARMS))
    return shas
