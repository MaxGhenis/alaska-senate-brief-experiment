"""Load and check the causal map in graph/graph.yaml.

A node is forecast once per *entry*: a (cell, state) pair from its `given` block that can occur. Cells
are certified shares, either points (0.505) or ranges ({label, lo, hi}: the share lies above lo and at
or below hi, except that the range just below 50% excludes 50% itself). A range is stored under a
representative share, `at` (its midpoint unless the map gives one; an open tail needs one), and keyed
by its label. States are named boolean conditions on other nodes. A pair whose state
cannot hold at that cell (a `failed` state at a passing cell, say) is not an entry. Conditions are a small expression
language (node ids, `passes`, `node.option` for a categorical node, `and`, `or`, `not`, parentheses)
parsed with `ast` and evaluated without `eval`.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = ROOT / "graph" / "graph.yaml"

KINDS = {"share_cdf", "quantity", "binary", "pmf", "categorical", "derived"}
BOOLEAN_KINDS = {"binary", "derived"}
CELL_MODES = {"none", "all", "pass", "near"}


class GraphError(ValueError):
    pass


@dataclass(frozen=True)
class Condition:
    """A parsed boolean expression over node ids, `passes` and `node.option` tests."""

    text: str
    tree: ast.AST = field(compare=False, repr=False)
    names: frozenset[str] = field(compare=False)
    # Bare names (booleans) and (node, option) tests on categorical nodes, checked by load().
    bare: frozenset[str] = field(compare=False, default=frozenset())
    options: frozenset[tuple[str, str]] = field(compare=False, default=frozenset())

    @classmethod
    def parse(cls, text: str) -> "Condition":
        try:
            tree = ast.parse(text, mode="eval").body
        except SyntaxError as exc:
            raise GraphError(f"bad condition {text!r}: {exc}") from exc
        bare: set[str] = set()
        options: set[tuple[str, str]] = set()

        def check(node: ast.AST) -> None:
            if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
                for value in node.values:
                    check(value)
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                check(node.operand)
            elif isinstance(node, ast.Name):
                bare.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                options.add((node.value.id, node.attr))
            else:
                raise GraphError(f"condition {text!r} uses {ast.dump(node)}; only and/or/not/names")

        check(tree)
        names = bare | {n for n, _ in options}
        return cls(text, tree, frozenset(names), frozenset(bare), frozenset(options))

    def evaluate(self, values: dict) -> bool:
        """`values` maps boolean nodes to bools and categorical nodes to an option (None if absent)."""

        def ev(node: ast.AST) -> bool:
            if isinstance(node, ast.BoolOp):
                parts = (ev(v) for v in node.values)
                return all(parts) if isinstance(node.op, ast.And) else any(parts)
            if isinstance(node, ast.UnaryOp):
                return not ev(node.operand)
            if isinstance(node, ast.Attribute):
                return values[node.value.id] == node.attr
            return bool(values[node.id])

        return ev(self.tree)

    def evaluate_vec(self, values: dict):
        """Elementwise evaluation over numpy boolean arrays."""
        import numpy as np

        def ev(node: ast.AST):
            if isinstance(node, ast.BoolOp):
                arrays = [ev(v) for v in node.values]
                op = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
                out = arrays[0]
                for a in arrays[1:]:
                    out = op(out, a)
                return out
            if isinstance(node, ast.UnaryOp):
                return np.logical_not(ev(node.operand))
            if isinstance(node, ast.Attribute):
                return np.asarray(values[node.value.id]) == node.attr
            return np.asarray(values[node.id], dtype=bool)

        return ev(self.tree)


@dataclass(frozen=True)
class Entry:
    """One forecast a node needs: at a cell (or none) and in a state (or none)."""

    cell: float | None
    state: str | None
    name: str | None = None  # a range cell's label; a point cell is labelled by cell_label

    @property
    def key(self) -> str:
        parts = []
        if self.cell is not None:
            parts.append(self.name or cell_label(self.cell))
        if self.state is not None:
            parts.append(self.state)
        return "|".join(parts) if parts else "all"


def cell_label(cell: float) -> str:
    """0.505 -> 'dem_50.5'; 0.6 -> 'dem_60.0'."""
    return f"dem_{cell * 100:.1f}"


@dataclass
class Node:
    id: str
    kind: str
    module: str
    question: str
    resolution: str = ""
    unit: str = ""
    integer: bool = False
    bounds: tuple[float, float] | None = None
    labels: list[str] | None = None
    options: dict[str, str] = field(default_factory=dict)
    continuous: bool = False
    scored: bool = True
    baseline: bool = False
    notes: str = ""
    exists_if: Condition | None = None
    rule: Condition | None = None
    cells_mode: str = "none"  # none | all | pass | near
    states: dict[str, Condition] = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def parents(self) -> set[str]:
        names: set[str] = set()
        for cond in [self.exists_if, self.rule, *self.states.values()]:
            if cond is not None:
                names |= cond.names
        names.discard("passes")
        return names

    @property
    def uses_cells(self) -> bool:
        return self.cells_mode != "none"


@dataclass
class Graph:
    measure: str
    election_date: str
    as_of: str
    fail_cells: list[float]
    pass_cells: list[float]
    bands: dict[str, tuple[float, float]]
    quantiles: list[float]
    share_grid: list[float]
    share_floor: float
    share_ceiling: float
    nodes: dict[str, Node]
    order: list[str]
    # Module id -> section title, in the order the prompt and the report show them.
    modules: dict[str, str] = field(default_factory=dict)
    # Range cells only: representative share -> its label, and -> its (lo, hi), None for an open end.
    cell_names: dict[float, str] = field(default_factory=dict)
    ranges: dict[float, tuple[float | None, float | None]] = field(default_factory=dict)
    _possible: dict = field(default_factory=dict, repr=False)

    def label(self, cell: float) -> str:
        """A cell's key in responses: a range's label, or cell_label for a point cell."""
        return self.cell_names.get(cell) or cell_label(cell)

    def closed(self, side: str) -> list[float]:
        """A side's cells with both ends finite ('fail' or 'pass'): every cell, for point cells."""
        cells = self.fail_cells if side == "fail" else self.pass_cells
        return [c for c in cells if None not in self.ranges.get(c, (0.0, 0.0))]

    @property
    def cells(self) -> list[float]:
        return [*self.fail_cells, *self.pass_cells]

    @property
    def near_cells(self) -> list[float]:
        """The two cells either side of 50%, for questions the margin barely moves."""
        return [max(self.fail_cells), min(self.pass_cells)]

    def cells_for(self, node: Node) -> list[float | None]:
        if node.cells_mode == "all":
            return self.cells
        if node.cells_mode == "pass":
            return list(self.pass_cells)
        if node.cells_mode == "near":
            return self.near_cells
        return [None]

    def owner(self, key: str) -> Node | None:
        """The node a summary key belongs to: 'ban_status.repealed' -> ban_status."""
        return self.nodes.get(key.split(".", 1)[0])

    def entries(self, node: Node) -> list[Entry]:
        if node.kind == "derived":
            return []
        states = list(node.states) or [None]
        return [Entry(c, s, self.cell_names.get(c) if c is not None else None)
                for c in self.cells_for(node) for s in states if self.possible(node, c, s)]

    def possible(self, node: Node, cell: float | None, state: str | None) -> bool:
        """Whether the node exists in this state at this cell in some consistent world."""
        key = (node.id, cell, state)
        if key not in self._possible:
            conds = [c for c in (node.exists_if, node.states.get(state) if state else None) if c is not None]
            if cell is None or not conds:
                self._possible[key] = True
            else:
                names = set().union(*(c.names for c in conds)) | {"passes"}
                self._possible[key] = any(
                    values["passes"] == (cell > 0.5) and all(c.evaluate(values) for c in conds)
                    for values in assignments(self, boolean_scope(self, names))
                )
        return self._possible[key]

    def forecast_nodes(self) -> list[Node]:
        return [self.nodes[i] for i in self.order if self.nodes[i].kind != "derived"]


def _node(raw: dict) -> Node:
    kind = raw.get("kind")
    if kind not in KINDS:
        raise GraphError(f"node {raw.get('id')!r}: unknown kind {kind!r}")
    given = raw.get("given") or {}
    cells_mode = given.get("cells", "none")
    if cells_mode not in CELL_MODES:
        raise GraphError(f"node {raw['id']}: given.cells must be one of {sorted(CELL_MODES - {'none'})}")
    states = {name: Condition.parse(text) for name, text in (given.get("states") or {}).items()}
    bounds = tuple(raw["bounds"]) if "bounds" in raw else None
    node = Node(
        id=raw["id"],
        kind=kind,
        module=raw.get("module", ""),
        question=raw["question"],
        resolution=raw.get("resolution", ""),
        unit=raw.get("unit", ""),
        integer=bool(raw.get("integer", False)),
        bounds=bounds,
        labels=raw.get("labels"),
        options=dict(raw.get("options") or {}),
        continuous=bool(raw.get("continuous", False)),
        scored=bool(raw.get("scored", kind != "derived")),
        baseline=bool(raw.get("baseline", False)),
        notes=raw.get("notes", ""),
        exists_if=Condition.parse(raw["exists_if"]) if raw.get("exists_if") else None,
        rule=Condition.parse(raw["rule"]) if raw.get("rule") else None,
        cells_mode=cells_mode,
        states=states,
        raw=raw,
    )
    if kind == "quantity" and (bounds is None or bounds[0] >= bounds[1]):
        raise GraphError(f"node {node.id}: a quantity needs bounds [lo, hi] with lo < hi")
    if kind == "pmf" and not node.labels:
        raise GraphError(f"node {node.id}: a pmf needs labels")
    if kind == "categorical" and (len(node.options) < 2 or not all(o.isidentifier() for o in node.options)):
        raise GraphError(f"node {node.id}: a categorical node needs two or more identifier options")
    if node.continuous and cells_mode != "all":
        raise GraphError(f"node {node.id}: continuous applies only to nodes forecast at all cells")
    if kind == "derived" and node.rule is None:
        raise GraphError(f"node {node.id}: a derived node needs a rule")
    if kind == "derived" and (states or cells_mode != "none"):
        raise GraphError(f"node {node.id}: a derived node takes no given block")
    return node


def _toposort(nodes: dict[str, Node]) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(nid: str, path: tuple[str, ...]) -> None:
        if state.get(nid) == 2:
            return
        if state.get(nid) == 1:
            raise GraphError(f"cycle: {' -> '.join((*path, nid))}")
        state[nid] = 1
        for parent in sorted(nodes[nid].parents):
            visit(parent, (*path, nid))
        state[nid] = 2
        order.append(nid)

    for nid in nodes:
        visit(nid, ())
    return order


def load(path: Path | str = GRAPH_PATH) -> Graph:
    raw = yaml.safe_load(Path(path).read_text())
    nodes: dict[str, Node] = {}
    for item in raw["nodes"]:
        node = _node(item)
        if node.id in nodes or node.id == "passes":
            raise GraphError(f"duplicate or reserved node id {node.id!r}")
        nodes[node.id] = node
    for node in nodes.values():
        for cond in [node.exists_if, node.rule, *node.states.values()]:
            if cond is None:
                continue
            for parent in cond.bare - {"passes"}:
                if parent not in nodes:
                    raise GraphError(f"node {node.id} refers to unknown node {parent!r}")
                if nodes[parent].kind not in BOOLEAN_KINDS:
                    raise GraphError(f"node {node.id} conditions on {parent}, which is not boolean")
            for parent, option in cond.options:
                if parent not in nodes or nodes[parent].kind != "categorical":
                    raise GraphError(f"node {node.id} tests {parent}.{option}, but {parent} is not categorical")
                if option not in nodes[parent].options:
                    raise GraphError(f"node {node.id} tests unknown option {parent}.{option}")
    fail, pas, names, ranges = _cells(raw["cells"])
    grid = [float(x) for x in raw["share_grid"]]
    if grid != sorted(set(grid)) or 0.5 not in grid or 0.49 not in grid or 0.51 not in grid:
        raise GraphError("share_grid must be strictly increasing and include 0.49, 0.50 and 0.51")
    if ranges and grid != sorted({end for pair in ranges.values() for end in pair if end is not None}):
        raise GraphError("share_grid must list exactly the range cells' finite ends, so that each range's "
                         "probability follows from the share forecast")
    graph = Graph(
        measure=raw["measure"],
        election_date=str(raw["election_date"]),
        as_of=str(raw["as_of"]),
        fail_cells=fail,
        pass_cells=pas,
        bands={k: (float(v[0]), float(v[1])) for k, v in raw["bands"].items()},
        quantiles=[float(q) for q in raw["quantiles"]],
        share_grid=grid,
        share_floor=float(raw["share_floor"]),
        share_ceiling=float(raw["share_ceiling"]),
        nodes=nodes,
        order=_toposort(nodes),
        modules=_modules(raw, nodes),
        cell_names=names,
        ranges=ranges,
    )
    if not graph.share_floor < grid[0] or not graph.share_ceiling > grid[-1]:
        raise GraphError("share_floor and share_ceiling must lie outside share_grid")
    check_pass_cells(graph)
    check_states(graph)
    return graph


LABEL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")


def _cells(raw: dict) -> tuple[list[float], list[float], dict[float, str], dict]:
    """The fail and pass cells, sorted, and for range cells their labels and (lo, hi) ends.

    Point cells are plain numbers below and above 0.5. Range cells are mappings {label, lo, hi, at}:
    they must tile the shares with no gap or overlap, the fail side ending at 0.5 and the pass side
    starting there, with at most an open tail at each end (no lo on the lowest, no hi on the highest).
    """
    sides = {side: list(raw[side]) for side in ("fail", "pass")}
    items = sides["fail"] + sides["pass"]
    if not items or not sides["fail"] or not sides["pass"]:
        raise GraphError("the map needs fail cells and pass cells")
    if all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in items):
        fail = sorted(float(c) for c in sides["fail"])
        pas = sorted(float(c) for c in sides["pass"])
        if max(fail) >= 0.5 or min(pas) <= 0.5:
            raise GraphError("fail cells must lie below 0.5 and pass cells above it")
        if len(set(fail + pas)) != len(fail + pas):
            raise GraphError("cells must be distinct")
        return fail, pas, {}, {}
    if not all(isinstance(c, dict) for c in items):
        raise GraphError("cells must be all numbers (points) or all mappings (ranges)")
    names: dict[float, str] = {}
    ranges: dict[float, tuple[float | None, float | None]] = {}
    out = {}
    for side in ("fail", "pass"):
        rows = []
        for c in sides[side]:
            if set(c) - {"label", "lo", "hi", "at"}:
                raise GraphError(f"range cell {c}: only label, lo, hi and at are allowed")
            label = c.get("label")
            if not isinstance(label, str) or not label or set(label) - LABEL_CHARS:
                raise GraphError(f"range cell {c}: label must be letters, digits, '_' and '.'")
            lo = None if c.get("lo") is None else float(c["lo"])
            hi = None if c.get("hi") is None else float(c["hi"])
            if lo is None and hi is None:
                raise GraphError(f"range cell {label}: needs lo or hi")
            if lo is not None and hi is not None and not lo < hi:
                raise GraphError(f"range cell {label}: lo must be below hi")
            if "at" in c:
                at = float(c["at"])
            elif lo is not None and hi is not None:
                at = (lo + hi) / 2
            else:
                raise GraphError(f"range cell {label}: an open range needs `at`")
            if (lo is not None and not at > lo) or (hi is not None and not at <= hi):
                raise GraphError(f"range cell {label}: at must lie above lo and at or below hi")
            if (side == "fail") != (at < 0.5) or at == 0.5:
                raise GraphError(f"range cell {label}: a {side} cell must lie {'below' if side == 'fail' else 'above'} 0.5")
            rows.append((at, label, lo, hi))
        rows.sort()
        for i, (at, label, lo, hi) in enumerate(rows):
            first, last = i == 0, i == len(rows) - 1
            if lo is None and not (side == "fail" and first):
                raise GraphError(f"range cell {label}: only the lowest fail range may be open below")
            if hi is None and not (side == "pass" and last):
                raise GraphError(f"range cell {label}: only the highest pass range may be open above")
            if not last and hi != rows[i + 1][2]:
                raise GraphError(f"range cells {label} and {rows[i + 1][1]} must meet")
        if side == "fail" and rows[-1][3] != 0.5:
            raise GraphError("the highest fail range must end at 0.5")
        if side == "pass" and rows[0][2] != 0.5:
            raise GraphError("the lowest pass range must start at 0.5")
        for at, label, lo, hi in rows:
            names[at] = label
            ranges[at] = (lo, hi)
        out[side] = [r[0] for r in rows]
    if len(set(names.values())) != len(names) or len(names) != len(items):
        raise GraphError("range cells need distinct labels and distinct `at` values")
    return out["fail"], out["pass"], names, ranges


def _modules(raw: dict, nodes: dict[str, Node]) -> dict[str, str]:
    """Section titles from the map's `modules` block; a module it leaves out is titled by its id."""
    titles = {str(k): str(v) for k, v in (raw.get("modules") or {}).items()}
    for node in nodes.values():
        if node.module and node.module not in titles:
            titles[node.module] = node.module.replace("_", " ").capitalize()
    unused = set(titles) - {n.module for n in nodes.values()}
    if unused:
        raise GraphError(f"modules {sorted(unused)} have no nodes")
    return titles


def boolean_scope(graph: Graph, names: set[str]) -> list[str]:
    """Every node (and `passes`) that the given names depend on, for enumeration."""
    seen: set[str] = set()
    stack = list(names)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        if name == "passes":
            continue
        node = graph.nodes[name]
        stack.extend(node.parents)
        if node.exists_if is not None or node.rule is not None:
            stack.append("passes")
    return sorted(seen)


def _domain(graph: Graph, name: str) -> list:
    node = graph.nodes.get(name)
    if node is not None and node.kind == "categorical":
        return [None, *node.options]
    return [False, True]


def assignments(graph: Graph, names: list[str]):
    """All assignments of `names` consistent with each node's exists_if and rule.

    Booleans take False or True; a categorical node takes one of its options, or None where it
    does not exist.
    """
    for combo in itertools.product(*(_domain(graph, n) for n in names)):
        values = dict(zip(names, combo))
        ok = True
        for name in graph.order:
            if name not in values:
                continue
            node = graph.nodes[name]
            if node.rule is not None and values[name] != node.rule.evaluate(values):
                ok = False
                break
            exists = node.exists_if is None or node.exists_if.evaluate(values)
            if node.kind == "categorical" and (values[name] is None) == exists:
                ok = False
                break
            if node.kind != "categorical" and values[name] and not exists:
                ok = False
                break
        if ok:
            yield values


def check_states(graph: Graph) -> None:
    """Each node's states must be exclusive and exhaustive wherever the node exists."""
    for node in graph.nodes.values():
        if not node.states:
            continue
        names = set().union(*(c.names for c in node.states.values()))
        if node.exists_if is not None:
            names |= node.exists_if.names
        scope = boolean_scope(graph, names)
        for values in assignments(graph, scope):
            if node.exists_if is not None and not node.exists_if.evaluate(values):
                continue
            hits = [s for s, cond in node.states.items() if cond.evaluate(values)]
            if len(hits) != 1:
                raise GraphError(
                    f"node {node.id}: states {sorted(node.states)} are not exclusive and exhaustive "
                    f"at {values} (matched {hits})"
                )


def check_pass_cells(graph: Graph) -> None:
    """A node forecast only at passing cells must be undefined wherever the measure fails.

    Otherwise a failing draw would silently take the passing cells' values.
    """
    for node in graph.nodes.values():
        if node.cells_mode != "pass":
            continue
        if node.exists_if is None:
            raise GraphError(f"node {node.id}: cells: pass needs an exists_if that implies passes")
        scope = boolean_scope(graph, set(node.exists_if.names) | {"passes"})
        for values in assignments(graph, scope):
            if node.exists_if.evaluate(values) and not values["passes"]:
                raise GraphError(f"node {node.id}: cells: pass, but exists_if holds at {values}")
