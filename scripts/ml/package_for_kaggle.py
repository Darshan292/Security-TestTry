#!/usr/bin/env python3
"""Build the Kaggle dataset that carries the ml/train package (no git clone is needed on Kaggle).

Output directory (default dist/kaggle_ml_train/):
    ml_train.zip             deterministic zip of ml/__init__.py and ml/train/*.py   (unzips to ml/train/...)
    pgw_ml_runtime.zip       deterministic zip of the gateway's ML runtime slot (pgw/__init__.py, pgw/types.py, pgw/ml/*.py),
                             so notebook 04 can load its exported bundles with the production classes
    ml/train/*.py            the same files unpacked (Kaggle may auto-extract zips; the notebooks accept either layout)
    dataset-metadata.json    for `kaggle datasets create -p <dir>`
    SHA256SUMS               hashes of the files above

Only Python sources of ml/train are packaged. The script refuses to package anything that could carry evaluation or
real data (jsonl/json/csv/gz/enc files, anything under eval/ or data/).

Usage:  python scripts/ml/package_for_kaggle.py --owner <kaggle-username> [--slug pgw-ml-train] [--out dist/kaggle_ml_train]
Then:   kaggle datasets create -p dist/kaggle_ml_train --dir-mode zip     (first time)
        kaggle datasets version -p dist/kaggle_ml_train -m "update" --dir-mode zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_SUFFIXES = {".jsonl", ".json", ".csv", ".tsv", ".gz", ".enc", ".pem", ".key", ".env", ".parquet", ".onnx", ".bin"}
EPOCH = (2026, 1, 1, 0, 0, 0)


def collect(root: Path = ROOT) -> list[tuple[str, Path]]:
    files = [("ml/__init__.py", root / "ml" / "__init__.py")]
    for p in sorted((root / "ml" / "train").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        files.append((p.relative_to(root).as_posix(), p))
    for rel, p in files:
        if p.suffix.lower() in FORBIDDEN_SUFFIXES or rel.startswith(("eval/", "data/")):
            raise SystemExit(f"refusing to package {rel}")
        if not p.exists():
            raise SystemExit(f"missing {rel}")
    return files


def collect_runtime(root: Path = ROOT) -> list[tuple[str, Path]]:
    files = [("pgw/__init__.py", root / "pgw" / "__init__.py"), ("pgw/types.py", root / "pgw" / "types.py")]
    files += [(p.relative_to(root).as_posix(), p) for p in sorted((root / "pgw" / "ml").glob("*.py"))]
    for rel, p in files:
        if not p.exists():
            raise SystemExit(f"missing {rel}")
    return files


def _zip(path: Path, files: list[tuple[str, Path]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in files:
            info = zipfile.ZipInfo(rel, EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build(out: Path, owner: str, slug: str, root: Path = ROOT) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}", slug) or not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", owner):
        raise SystemExit("owner/slug must be a Kaggle username and a lower-case slug")
    files = collect(root)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    zpath = out / "ml_train.zip"
    _zip(zpath, files)
    _zip(out / "pgw_ml_runtime.zip", collect_runtime(root))
    for rel, p in files:
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    meta = {"title": "PGW ML train package", "id": f"{owner}/{slug}", "licenses": [{"name": "other"}],
            "subtitle": "Synthetic-data training code for the privacy gateway ML detectors (no data inside)",
            "description": "Pure-Python package ml/train (value generators, template slot filling, BIO alignment) and the gateway ML runtime classes (pgw_ml_runtime.zip). Contains no data."}
    (out / "dataset-metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    sums = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.relative_to(out).as_posix()}\n" for p in sums), encoding="utf-8")
    return {"out": str(out), "files": len(files), "zip_sha256": sha256(zpath), "runtime_zip_sha256": sha256(out / "pgw_ml_runtime.zip"), "dataset": meta["id"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="Kaggle username that will own the dataset")
    ap.add_argument("--slug", default="pgw-ml-train")
    ap.add_argument("--out", default=str(ROOT / "dist" / "kaggle_ml_train"))
    a = ap.parse_args(argv)
    info = build(Path(a.out), a.owner, a.slug)
    print(json.dumps(info, indent=2))
    print(f"next: kaggle datasets create -p {info['out']} --dir-mode zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
