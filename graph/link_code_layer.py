"""Merge the provenance graph with Graphify's tree-sitter code graph without losing edges.

    python graph/link_code_layer.py <graphify-out/graph.json>   # -> graph/merged.graph.json

`graphify merge-graphs` prefixes ids and collapses parallel same-endpoint edges. That is fine
for code, but it would silently drop provenance edges (a claim can both bear on and challenge
one decision). So this script keeps provenance ids as-is, namespaces code ids as `code::…`,
keeps every edge (multigraph), and adds explicit EXTRACTED links:
  K-component --implemented_in--> code file node
  X-test      --is_code--> test function node
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(code_graph: str) -> None:
    prov = json.loads((ROOT / "graph/provenance.graph.json").read_text())
    code = json.loads(Path(code_graph).read_text())

    nodes = [dict(n) for n in prov["nodes"]]
    links = [dict(l) for l in prov["links"]]
    by_file: dict[str, str] = {}
    funcs: dict[tuple[str, str], str] = {}
    symbols: dict[tuple[str, str], str] = {}
    for n in code["nodes"]:
        m = dict(n)
        m["id"] = "code::" + n["id"]
        m["_origin"] = "graphify-ast"
        nodes.append(m)
        sf = n.get("source_file") or ""
        if n.get("file_type") == "code" and sf and n.get("label") == Path(sf).name:
            by_file[sf] = m["id"]
        label = (n.get("label") or "").rstrip("()")
        if sf:
            symbols[(sf, label)] = m["id"]
        if sf.startswith("tests/") and label.startswith("test_"):
            funcs[(sf, label)] = m["id"]
    for l in code["links"]:
        m = dict(l)
        m["source"], m["target"] = "code::" + l["source"], "code::" + l["target"]
        m["_origin"] = "graphify-ast"
        links.append(m)

    unlinked = []
    for n in prov["nodes"]:
        cf = n.get("code_file")
        if not cf:
            continue
        if n["id"].startswith("K-") and cf in by_file:
            symbol = n["id"].split(".", 1)[1] if "." in n["id"] else None   # K-edm.RosterIndex -> RosterIndex
            target = symbols.get((cf, symbol)) if symbol else None
            links.append({"source": n["id"], "target": target or by_file[cf], "relation": "implemented_in",
                          "confidence": "EXTRACTED", "_origin": "link", "weight": 1.0})
        elif n["id"].startswith("X-"):
            name = n["id"][2:]
            hits = [v for (f, lab), v in funcs.items() if f == cf and lab.startswith(name.rstrip("_*"))]
            target = hits[0] if hits else by_file.get(cf)
            if target:
                links.append({"source": n["id"], "target": target, "relation": "is_code",
                              "confidence": "EXTRACTED", "_origin": "link", "weight": 1.0})
            else:
                unlinked.append(n["id"])
        else:
            unlinked.append(n["id"])

    out = {"directed": True, "multigraph": True, "graph": {"name": "esu-provenance+code"},
           "nodes": nodes, "links": links}
    (ROOT / "graph/merged.graph.json").write_text(json.dumps(out, ensure_ascii=False))
    n_link = sum(1 for l in links if l.get("_origin") == "link")
    print(f"merged: {len(nodes)} nodes, {len(links)} edges "
          f"(provenance {len(prov['links'])}, code {len(code['links'])}, cross-layer {n_link}); "
          f"unlinked: {unlinked or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "graphify-out/graph.json")
