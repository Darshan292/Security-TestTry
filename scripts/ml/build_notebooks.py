#!/usr/bin/env python3
"""Build the Kaggle notebooks from the percent-format sources in notebooks/kaggle/src/.

Sources are plain Python files (`# %%` starts a code cell, `# %% [markdown]` a markdown cell whose lines are `# `-prefixed,
`# %% include FILE` inlines the cells of another source file). Writing notebooks as .py keeps them lintable and diffable;
this script emits the .ipynb and kernel-metadata.json that `kaggle kernels push -p <dir>` expects.

Usage: python scripts/ml/build_notebooks.py [--owner your-kaggle-username] [--check]
  --check   exit 1 if the committed notebooks differ from what the sources would generate (used by the tests)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NB_DIR = ROOT / "notebooks" / "kaggle"
SRC = NB_DIR / "src"
DEFAULT_OWNER = "your-kaggle-username"
PKG_DATASET, TPL_DATASET = "pgw-ml-train", "pgw-templates"
NOTEBOOKS = {
    "01_generate_templates": ("nb01_generate_templates.py", "PGW 01 Generate templates", [PKG_DATASET]),
    "02_train_token_classifier": ("nb02_train_token_classifier.py", "PGW 02 Train token classifier", [PKG_DATASET, TPL_DATASET]),
    "03_train_content_gate": ("nb03_train_content_gate.py", "PGW 03 Train content gate", [PKG_DATASET, TPL_DATASET]),
}


def parse_cells(path: Path) -> list[tuple[str, str]]:
    cells: list[tuple[str, str]] = []
    kind, title, buf = None, "", []

    def flush():
        if kind is None:
            return
        body = "\n".join(buf).strip("\n")
        if kind == "markdown":
            body = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in body.splitlines())
        elif title:
            body = f"# {title}\n{body}"
        if body.strip():
            cells.append((kind, body))

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# %%"):
            flush()
            rest = line[4:].strip()
            buf = []
            if rest.startswith("include "):
                cells.extend(parse_cells(path.parent / rest.split(None, 1)[1]))
                kind, title = None, ""
            elif rest.startswith("[markdown]"):
                kind, title = "markdown", ""
            else:
                kind, title = "code", rest
        else:
            buf.append(line)
    flush()
    return cells


def notebook_json(cells: list[tuple[str, str]], name: str) -> dict:
    out = []
    for i, (kind, body) in enumerate(cells):
        cid = hashlib.sha256(f"{name}:{i}".encode()).hexdigest()[:12]
        cell = {"cell_type": kind, "id": cid, "metadata": {}, "source": body.splitlines(keepends=True)}
        if kind == "code":
            cell.update({"execution_count": None, "outputs": []})
        out.append(cell)
    return {"cells": out, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                        "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


def render(owner: str) -> dict[Path, str]:
    files: dict[Path, str] = {}
    for slug, (src, title, datasets) in NOTEBOOKS.items():
        cells = parse_cells(SRC / src)
        d = NB_DIR / slug
        files[d / f"{slug}.ipynb"] = json.dumps(notebook_json(cells, slug), indent=1, ensure_ascii=False) + "\n"
        meta = {"id": f"{owner}/{slug.replace('_', '-')}", "title": title, "code_file": f"{slug}.ipynb", "language": "python",
                "kernel_type": "notebook", "is_private": "true", "enable_gpu": "true", "enable_tpu": "false", "enable_internet": "true",
                "dataset_sources": [f"{owner}/{ds}" for ds in datasets], "competition_sources": [], "kernel_sources": [], "model_sources": []}
        files[d / "kernel-metadata.json"] = json.dumps(meta, indent=2) + "\n"
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    files = render(a.owner)
    if a.check:
        stale = [str(p.relative_to(ROOT)) for p, t in files.items() if not p.exists() or p.read_text(encoding="utf-8") != t]
        if stale:
            print("stale (run scripts/ml/build_notebooks.py): " + ", ".join(stale), file=sys.stderr)
            return 1
        return 0
    for p, t in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
        print("wrote", p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
