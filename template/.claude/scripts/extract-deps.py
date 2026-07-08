#!/usr/bin/env python
"""extract-deps.py — deterministic task-dependency extractor (Architecture.md §5).

Turns the planner's per-task file footprints into a scheduling DAG. This is the
one component with real unit tests, because it is pure computation: given the
same plan.md and the same codebase graph it MUST emit a byte-identical
task-graph.json. Nothing here calls an LLM — that is the whole point of taking
dependency math away from the agents.

Usage
    extract-deps.py --plan specs/<f>/plan.md --out specs/<f>/task-graph.json
    extract-deps.py --files src/auth/ src/db/schema.prisma   # standalone "what does this touch?"

Options
    --graphify-out graphify-out/     structural graph directory (default)
    --depth 1                        hops to expand each footprint (default 1)

Footprint source of truth — plan.md declares each task in a fenced ```task block:

    ```task
    id: db-orders-migration
    agent: specialist-database
    footprint:
      - db/migrations/
      - db/schema.prisma
    produces: [migration]     # optional interface(s) this task defines
    consumes: []              # optional interface(s) this task depends on
    ```

Edges (a sequential dependency = the two tasks may NOT run concurrently):
  * overlap            — a footprint file is literally in both tasks
  * shared_dependent   — the graph expands both onto a common third file
  * contract           — one task consumes an interface another produces
Fully disjoint expanded footprints and no contract link => parallel-safe (no edge).
Direction: contract producer->consumer; otherwise earlier-in-plan -> later (a
total order, so the graph is always a DAG).
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys


# --------------------------------------------------------------------------- #
# plan.md parsing — a tiny, dependency-free subset parser for ```task blocks
# --------------------------------------------------------------------------- #
TASK_BLOCK_RE = re.compile(r"```task\s*\n(.*?)```", re.S)


def _parse_list(inline):
    inline = inline.strip()
    if inline.startswith("[") and inline.endswith("]"):
        inner = inline[1:-1].strip()
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    return []


def parse_plan(text):
    """Return an ordered list of task dicts: id, agent, footprint, produces, consumes."""
    tasks = []
    for block in TASK_BLOCK_RE.findall(text):
        task = {"id": None, "agent": None, "footprint": [], "produces": [], "consumes": []}
        key = None
        for raw in block.splitlines():
            line = raw.rstrip()
            if not line.strip():
                continue
            m = re.match(r"^(\w+):\s*(.*)$", line)
            if m and not line.startswith((" ", "\t", "-")):
                key, val = m.group(1), m.group(2).strip()
                if key in ("footprint", "produces", "consumes"):
                    task[key] = _parse_list(val) if val else []
                elif key in ("id", "agent"):
                    task[key] = val.strip("'\"")
                continue
            item = re.match(r"^\s*-\s*(.+)$", line)
            if item and key in ("footprint", "produces", "consumes"):
                task[key].append(item.group(1).strip().strip("'\""))
        if task["id"]:
            task["footprint"] = [norm(p) for p in task["footprint"]]
            tasks.append(task)
    return tasks


def norm(p):
    return p.replace("\\", "/").strip()


# --------------------------------------------------------------------------- #
# graphify structural graph — defensive loader + undirected BFS expansion
# --------------------------------------------------------------------------- #
def load_graph(graphify_out):
    path = os.path.join(graphify_out, "graph.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def node_file(node):
    for k in ("file", "path", "source_location", "location", "filepath"):
        v = node.get(k) if isinstance(node, dict) else None
        if isinstance(v, str) and v:
            return norm(v.split(":", 1)[0])
    return None


def build_index(graph):
    """Return (node_file_map, adjacency) from a best-effort read of graph.json."""
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    files, adj = {}, {}
    for n in nodes:
        nid = n.get("id") if isinstance(n, dict) else None
        if nid is None:
            continue
        files[nid] = node_file(n)
        adj.setdefault(nid, set())
    for e in graph.get("edges", []) if isinstance(graph, dict) else []:
        if isinstance(e, dict):
            a, b = e.get("source", e.get("from")), e.get("target", e.get("to"))
        elif isinstance(e, (list, tuple)) and len(e) >= 2:
            a, b = e[0], e[1]
        else:
            continue
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    return files, adj


def matches(fpath, entry):
    if fpath is None:
        return False
    entry = norm(entry)
    if entry.endswith("/"):
        return fpath.startswith(entry)
    if any(c in entry for c in "*?["):
        return fnmatch.fnmatch(fpath, entry)
    return fpath == entry or fpath.startswith(entry + "/")


def expand(footprint, files, adj, depth):
    """Expand a footprint to a sorted file set via <=depth undirected hops."""
    if not files:
        return sorted(set(footprint))
    seeds = {nid for nid, f in files.items() if any(matches(f, e) for e in footprint)}
    frontier, seen = set(seeds), set(seeds)
    for _ in range(max(0, depth)):
        nxt = set()
        for nid in frontier:
            nxt |= adj.get(nid, set())
        nxt -= seen
        seen |= nxt
        frontier = nxt
    expanded = {files[nid] for nid in seen if files.get(nid)}
    return sorted(expanded | set(footprint))


# --------------------------------------------------------------------------- #
# edge computation
# --------------------------------------------------------------------------- #
def common_key(a_expanded, b_expanded):
    """First shared file/dir between two expanded sets, deterministically."""
    for x in sorted(a_expanded):
        for y in sorted(b_expanded):
            if x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/"):
                return x if len(x) <= len(y) else y
    return None


def compute_edges(tasks):
    order = {t["id"]: i for i, t in enumerate(tasks)}
    raw = {t["id"]: set(t["footprint"]) for t in tasks}
    exp = {t["id"]: set(t["_expanded"]) for t in tasks}
    edges = {}

    # contract edges: producer -> consumer
    for p in tasks:
        for c in tasks:
            if p["id"] == c["id"]:
                continue
            shared = set(p.get("produces", [])) & set(c.get("consumes", []))
            for iface in sorted(shared):
                edges[(p["id"], c["id"])] = "contract: %s" % iface

    # footprint edges: overlap / shared_dependent, directed by plan order
    ids = [t["id"] for t in tasks]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            key = common_key(exp[a], exp[b])
            if key is None:
                continue
            frm, to = (a, b) if order[a] <= order[b] else (b, a)
            if (frm, to) in edges or (to, frm) in edges:
                continue  # a contract edge already orders this pair
            in_raw = any(matches(f, key) or matches(key, f) for f in raw[a]) and \
                     any(matches(f, key) or matches(key, f) for f in raw[b])
            reason = ("overlap: %s" if in_raw else "shared_dependent: %s") % key
            edges[(frm, to)] = reason

    return [{"from": f, "to": t, "reason": r} for (f, t), r in
            sorted(edges.items(), key=lambda kv: kv[0])]


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def build_task_graph(plan_path, graphify_out, depth):
    text = open(plan_path, "r", encoding="utf-8").read()
    tasks = parse_plan(text)
    graph = load_graph(graphify_out)
    files, adj = build_index(graph) if graph else ({}, {})

    for t in sorted(tasks, key=lambda x: x["id"]):
        t["_expanded"] = expand(t["footprint"], files, adj, depth)

    tasks_sorted = sorted(tasks, key=lambda x: x["id"])
    out_tasks = [{
        "id": t["id"], "agent": t.get("agent"),
        "footprint": sorted(t["footprint"]),
        "expanded_footprint": t["_expanded"],
        "produces": sorted(t.get("produces", [])),
        "consumes": sorted(t.get("consumes", [])),
    } for t in tasks_sorted]

    gv = "none"
    if isinstance(graph, dict):
        gv = str(graph.get("graphify_version") or graph.get("version") or "unknown")

    return {
        "generated_from": {
            "plan_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "graphify_version": gv,
            "dependency_depth": depth,
        },
        "tasks": out_tasks,
        "edges": compute_edges(tasks_sorted),
    }


def dumps_stable(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deterministic task-dependency extractor")
    ap.add_argument("--plan")
    ap.add_argument("--out")
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--graphify-out", default="graphify-out")
    ap.add_argument("--depth", type=int, default=None)
    args = ap.parse_args(argv)

    depth = args.depth if args.depth is not None else 1

    if args.files:
        graph = load_graph(args.graphify_out)
        files, adj = build_index(graph) if graph else ({}, {})
        touched = expand([norm(f) for f in args.files], files, adj, depth)
        report = {"input": [norm(f) for f in args.files],
                  "depth": depth,
                  "graph": "present" if graph else "absent",
                  "touched": touched}
        sys.stdout.write(dumps_stable(report))
        return 0

    if not (args.plan and args.out):
        ap.error("provide either --files, or both --plan and --out")

    result = build_task_graph(args.plan, args.graphify_out, depth)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(dumps_stable(result))
    sys.stderr.write("task-graph.json: %d tasks, %d edges\n"
                     % (len(result["tasks"]), len(result["edges"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
