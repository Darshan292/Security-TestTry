"""Deterministic provenance graph over the research-to-decision files.

    python graph/build_provenance_graph.py            # writes graph/provenance.graph.json + graph/GRAPH_REPORT.md

Every node comes from an explicit ID in a markdown table row; every edge comes from an
explicit ID appearing in a named column of that row. Nothing is inferred, so every link
is tagged confidence=EXTRACTED with its file and line. Output uses Graphify's node-link
graph.json schema so it can be merged with Graphify's tree-sitter code graph
(`graphify merge-graphs`) and navigated with `graphify path` / `graphify explain`.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ID_RE = re.compile(
    r"\b(S-\d{2,3}|C-\d{2}|M-\d{2}|A-\d{2}|Q-\d{2}[a-z]?|PD-\d{1,2}|ADR-\d{4}|T-?\d{1,2}|W-?\d{1,2}|F-\d{1,2}|N-\d|K-[\w.]+)\b"
)
KIND = {"S": "source", "C": "claim", "M": "measurement", "A": "algorithm", "Q": "question", "PD": "proposed_decision",
        "ADR": "decision", "T": "threat", "W": "weakness", "F": "requirement", "N": "requirement", "K": "component",
        "X": "test"}

# component -> code file (links the provenance layer to Graphify's code layer)
COMPONENT_FILES = {
    "K-normalize": "pgw/normalize.py", "K-recognizers": "pgw/detect/recognizers.py", "K-edm": "pgw/detect/edm.py",
    "K-edm.RosterIndex": "pgw/detect/edm.py", "K-edm.RestoreLedger": "pgw/detect/edm.py",
    "K-alias": "pgw/alias.py", "K-policy": "pgw/policy.py", "K-pipeline": "pgw/pipeline.py",
    "K-egress": "pgw/egress.py", "K-stream": "pgw/stream.py", "K-gateway.app": "pgw/gateway/app.py",
    "K-gateway.upstream": "pgw/gateway/upstream.py",
}


def norm_id(raw: str) -> str:
    m = re.fullmatch(r"([TW])-?(\d+)", raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2))}"
    return raw


def id_kind(i: str) -> str:
    if i.startswith("PD-"):
        return KIND["PD"]
    if i.startswith("ADR-"):
        return KIND["ADR"]
    return KIND.get(i.split("-")[0], "other")


def tables(path: Path):
    """Yield (header_cells, row_cells, line_no) for every markdown table row."""
    header = None
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        if header is None:
            header = cells
            continue
        yield header, cells, n


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.links: list[dict] = []
        self.referenced: dict[str, set[str]] = defaultdict(set)

    def node(self, i: str, **attrs):
        n = self.nodes.setdefault(i, {"id": i, "label": i, "file_type": id_kind(i), "_origin": "provenance"})
        for k, v in attrs.items():
            if v and (k not in n or n[k] in ("", i)):
                n[k] = v
        return n

    def link(self, s: str, t: str, relation: str, src_file: str, line: int, **extra):
        if s == t:
            return
        self.referenced[t].add(f"{src_file}:{line}")
        self.referenced[s].add(f"{src_file}:{line}")
        self.links.append({"source": s, "target": t, "relation": relation, "confidence": "EXTRACTED",
                           "source_file": src_file, "source_location": f"L{line}", "weight": 1.0,
                           "_origin": "provenance", **extra})


def ids_in(text: str) -> list[str]:
    out = []
    for m in ID_RE.finditer(text):
        i = norm_id(m.group(1))
        if i.startswith("K-"):
            i = i.rstrip(".")
        out.append(i)
    return out


def tests_in(text: str) -> list[str]:
    return [f"X-{t}" for t in re.findall(r"(test_[a-z0-9_]+)", text)]


CLASS_RE = re.compile(r"FACT-P|FACT-M|CLAIM-S|CLAIM-V|KNOWN|INFERENCE|UNVERIFIED")


def build() -> Graph:
    g = Graph()
    R = ROOT / "research"

    # --- Sources
    for h, c, n in tables(R / "SOURCE_REGISTER.md"):
        if c and re.fullmatch(r"S-\d+", c[0]) and len(c) >= 7:
            col = dict(zip(h, c))
            g.node(c[0], label=f"{c[0]} {c[1][:80]}", kind=col.get("Kind", ""),
                   access_grade=col.get("Access grade", ""), url=col.get("URL", "").strip("`"),
                   epistemic_class={"P": "FACT-P", "I": "FACT-M", "S": "CLAIM-S", "N": "UNVERIFIED"}.get(
                       col.get("Access grade", "").strip(), ""),
                   source_file="research/SOURCE_REGISTER.md", source_location=f"L{n}")

    # --- Measurements and claims
    f = "research/EVIDENCE_MATRIX.md"
    for h, c, n in tables(R / "EVIDENCE_MATRIX.md"):
        col = dict(zip(h, c))
        if c and re.fullmatch(r"M-\d+", c[0]):
            g.node(c[0], label=f"{c[0]} {c[1][:70]}: {c[2][:60]}", epistemic_class="FACT-M",
                   source_file=f, source_location=f"L{n}")
            for t in ids_in(col.get("Supports / refutes", "")):
                g.link(c[0], t, "bears_on", f, n)
        elif c and re.fullmatch(r"C-\d+", c[0]):
            cls = CLASS_RE.findall(col.get("Class", ""))
            g.node(c[0], label=f"{c[0]} {c[1][:90]}", epistemic_class="/".join(dict.fromkeys(cls)) or "UNCLASSIFIED",
                   source_file=f, source_location=f"L{n}")
            for s in ids_in(col.get("Source(s)", "")):
                if s.startswith("S-"):
                    g.link(s, c[0], "asserts", f, n)
            for t in ids_in(col.get("→ Bears on", "")):
                g.link(c[0], t, "bears_on", f, n)
        elif "External claim" in h[0]:
            verdict = col.get("Verdict", "")
            rel = "contradicts" if re.search(r"less safe|refut|false|contradict", verdict, re.I) else \
                  "leaves_open" if re.search(r"open|not measured", verdict, re.I) else "corroborates"
            for m in [i for i in ids_in(col.get("Our measurement", "")) if i.startswith("M-")]:
                for cl in [i for i in ids_in(c[0]) if i.startswith("C-")]:
                    g.link(m, cl, rel, f, n, verdict=re.sub(r"\*", "", verdict)[:160])

    # --- Algorithms
    f = "research/ALGORITHM_ANALYSIS.md"
    for h, c, n in tables(R / "ALGORITHM_ANALYSIS.md"):
        if c and re.fullmatch(r"A-\d+", c[0]):
            col = dict(zip(h, c))
            g.node(c[0], label=f"{c[0]} {c[1][:80]}", status=col.get("Status in code (`a51c22d`)", ""),
                   verdict=col.get("Verdict", ""), epistemic_class="DECISION",
                   source_file=f, source_location=f"L{n}")
            for k in ids_in(col.get("Status in code (`a51c22d`)", "")):
                if k.startswith("K-"):
                    g.link(c[0], k, "implemented_by", f, n)
            for e in ids_in(col.get("Strongest evidence", "") + " " + col.get("Verdict", "")):
                g.link(e, c[0], "evidence_for" if e[0] in "MCS" else "relates_to", f, n)

    # --- Open questions
    f = "research/OPEN_QUESTIONS.md"
    for h, c, n in tables(R / "OPEN_QUESTIONS.md"):
        col = dict(zip(h, c))
        if c and re.fullmatch(r"Q-\d+[a-z]?", c[0]):
            g.node(c[0], label=f"{c[0]} {c[1][:90]}", priority=col.get("P", ""), owner=col.get("Owner", ""),
                   epistemic_class="OPEN" if "Closed" not in h[-1] else "CLOSED",
                   source_file=f, source_location=f"L{n}")
            for t in ids_in(col.get("Blocks", "")):
                g.link(c[0], t, "blocks", f, n)
            for e in ids_in(col.get("Current best evidence (class)", "") + col.get("Closed by", "")):
                g.link(e, c[0], "informs", f, n)

    # --- Decisions
    f = "research/ARCHITECTURE_DECISIONS.md"
    rel_by_col = {"Evidence for": ("supports", "in"), "Evidence against / risk": ("challenges", "in"),
                  "Threats": ("mitigates", "out"), "Reqs": ("satisfies", "out"),
                  "Components": ("implemented_by", "out"), "Tests / gates": ("tested_by", "out"),
                  "Metrics": ("measured_by", "out")}
    for h, c, n in tables(R / "ARCHITECTURE_DECISIONS.md"):
        col = dict(zip(h, c))
        if c and re.fullmatch(r"ADR-\d{4}", c[0]) and "Status" in col:
            g.node(c[0], label=f"{c[0]} {col.get('Decision (one line)', '')[:90]}",
                   status=re.sub(r"\*", "", col["Status"]), epistemic_class="DECISION",
                   source_file=f, source_location=f"L{n}")
            for column, (rel, direction) in rel_by_col.items():
                text = col.get(column, "")
                targets = ids_in(text) + (tests_in(text) if column == "Tests / gates" else [])
                for t in targets:
                    if t == c[0]:
                        continue
                    if direction == "in":
                        g.link(t, c[0], rel, f, n)
                    else:
                        g.link(c[0], t, rel, f, n)
        elif c and re.fullmatch(r"ADR-\d{4}", c[0]) and "Change" in col:
            for e in ids_in(col.get("Evidence", "")):
                g.link(e, c[0], "requires_amendment", f, n, change=col["Change"][:160])
        elif c and re.fullmatch(r"PD-\d{1,2}", c[0]):
            g.node(c[0], label=f"{c[0]} {c[1][:90]}", epistemic_class="PROPOSED",
                   source_file=f, source_location=f"L{n}")
            for e in ids_in(col.get("Why (evidence)", "")):
                g.link(e, c[0], "motivates", f, n)
            for w in ids_in(col.get("Closes", "")):
                g.link(c[0], w, "closes", f, n)

    # --- Weaknesses (SECURITY_EVALUATION §5)
    for f in ("SECURITY_EVALUATION.md", "SECURITY_EVALUATION_R2.md"):
      for h, c, n in tables(ROOT / f):
        if c and re.fullmatch(r"W\d{1,2}", c[0]):
            w = norm_id(c[0])
            g.node(w, label=f"{w} {re.sub(r'[*`]', '', c[1])[:90]}", severity=c[3] if len(c) > 3 else "",
                   epistemic_class="FACT-M", source_file=f, source_location=f"L{n}")
            for m in ids_in(" ".join(c[2:])):
                if m.startswith("M-"):
                    g.link(m, w, "evidences", f, n)

    # --- Threats (THREAT_MODEL) and requirements (DESIGN §2)
    for h, c, n in tables(ROOT / "THREAT_MODEL.md"):
        if c and re.fullmatch(r"T\d+", c[0]):
            t = norm_id(c[0])
            g.node(t, label=f"{t} {c[1][:90]}", source_file="THREAT_MODEL.md", source_location=f"L{n}")
            for a in ids_in(" ".join(c[2:])):
                if a.startswith("ADR-"):
                    g.link(t, a, "controlled_by", "THREAT_MODEL.md", n)
    for h, c, n in tables(ROOT / "DESIGN.md"):
        m = re.match(r"([FN]-\d+)\b", c[0]) if c else None
        if m:
            g.node(m.group(1), label=f"{m.group(1)} {c[1][:90]}", source_file="DESIGN.md", source_location=f"L{n}")

    # --- Components and tests: attach to code
    test_index = {}
    for tf in (ROOT / "tests").glob("test_*.py"):
        for ln, line in enumerate(tf.read_text().splitlines(), 1):
            m = re.match(r"def (test_[a-z0-9_]+)", line)
            if m:
                test_index[m.group(1)] = (f"tests/{tf.name}", ln)
    for i in list(g.nodes) + [l["target"] for l in g.links] + [l["source"] for l in g.links]:
        if i.startswith("K-"):
            g.node(i, file_type="component", code_file=COMPONENT_FILES.get(i, ""), epistemic_class="FACT-M")
        if i.startswith("X-"):
            name = i[2:]
            hit = next(((k, v) for k, v in test_index.items() if k.startswith(name.rstrip("_*"))), None)
            if hit is None and (ROOT / "tests" / f"{name}.py").exists():
                hit = (name, (f"tests/{name}.py", 1))
            g.node(i, file_type="test", code_file=hit[1][0] if hit else "",
                   source_location=f"L{hit[1][1]}" if hit else "", resolved=bool(hit))
    return g


def validate(g: Graph) -> list[str]:
    out = []
    defined = {i for i, n in g.nodes.items() if n.get("source_file")}
    refs = {l["target"] for l in g.links} | {l["source"] for l in g.links}
    dangling = sorted(i for i in refs - defined if not i.startswith(("K-", "X-")))
    out.append(f"### Dangling references ({len(dangling)})\nIDs referenced but not defined in any table: "
               + (", ".join(dangling) or "none"))
    unresolved = sorted(i for i, n in g.nodes.items() if i.startswith("X-") and not n.get("resolved"))
    out.append(f"### Test references not found in tests/ ({len(unresolved)})\n" + (", ".join(unresolved) or "none"))

    # decisions: FACT support
    lines = ["| Decision | Status | FACT-P/FACT-M support | Weak support used (CLAIM-S / KNOWN / UNVERIFIED / CLAIM-V) | Challenged by |",
             "|---|---|---|---|---|"]
    for i in sorted(x for x in g.nodes if x.startswith("ADR-")):
        sup = [l["source"] for l in g.links if l["target"] == i and l["relation"] in ("supports",)]
        strong = [s for s in sup if g.nodes.get(s, {}).get("epistemic_class", "").startswith(("FACT-P", "FACT-M"))]
        weak = [s for s in sup if s not in strong and s.startswith(("C-", "S-"))]
        ch = [l["source"] for l in g.links if l["target"] == i and l["relation"] in ("challenges", "requires_amendment")]
        weak_s = ", ".join("%s (%s)" % (w, g.nodes.get(w, {}).get("epistemic_class", "?")) for w in weak) or "—"
        status = g.nodes[i].get("status", "")[:40]
        lines.append(f"| {i} | {status} | {', '.join(strong) or '**none**'} | {weak_s} | {', '.join(sorted(set(ch))) or '—'} |")
    out.append("### Decision support audit (rule: every DECISION needs ≥ 1 FACT-P/FACT-M or is provisional)\n" + "\n".join(lines))

    bad = [(l["source"], l["target"], l["relation"]) for l in g.links
           if l["relation"] in ("supports", "bears_on", "evidence_for", "motivates")
           and g.nodes.get(l["source"], {}).get("epistemic_class", "") in ("UNVERIFIED", "CLAIM-V")]
    out.append(f"### Weak evidence pointed at decisions ({len(bad)})\n" +
               ("\n".join(f"- {s} ({g.nodes[s]['epistemic_class']}) —{r}→ {t}" for s, t, r in bad) or "none"))

    uncovered = [w for w in sorted(g.nodes) if w.startswith("W-")
                 and not any(l["target"] == w and l["relation"] == "closes" for l in g.links)]
    out.append(f"### Measured weaknesses with no proposed decision ({len(uncovered)})\n" + (", ".join(uncovered) or "none"))
    orphan_src = [s for s in g.nodes if s.startswith("S-") and not any(l["source"] == s for l in g.links)]
    out.append(f"### Sources not linked to any claim ({len(orphan_src)} of "
               f"{sum(1 for s in g.nodes if s.startswith('S-'))})\nExpected: the register lists every citation; "
               "only decision-relevant ones are lifted into EVIDENCE_MATRIX claims.")
    return out


def main() -> None:
    g = build()
    out_dir = ROOT / "graph"
    data = {"directed": True, "multigraph": False, "graph": {"name": "esu-provenance"},
            "nodes": list(g.nodes.values()), "links": g.links}
    (out_dir / "provenance.graph.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    kinds = Counter(n["file_type"] for n in g.nodes.values())
    rels = Counter(l["relation"] for l in g.links)
    classes = Counter(n.get("epistemic_class", "—") for n in g.nodes.values())
    report = ["# Provenance graph report", "",
              "Generated by `graph/build_provenance_graph.py`; do not edit by hand. Every edge is EXTRACTED from an "
              "explicit ID in a table row (file + line kept on the edge). No inferred edges.", "",
              f"**Nodes:** {len(g.nodes)}: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()),
              f"**Edges:** {len(g.links)}: " + ", ".join(f"{k} {v}" for k, v in rels.most_common()),
              f"**Epistemic classes:** " + ", ".join(f"{k} {v}" for k, v in classes.most_common()), ""]
    report += validate(g)
    (out_dir / "GRAPH_REPORT.md").write_text("\n\n".join(report) + "\n")
    print(f"{len(g.nodes)} nodes, {len(g.links)} edges -> graph/provenance.graph.json, graph/GRAPH_REPORT.md")


if __name__ == "__main__":
    main()
