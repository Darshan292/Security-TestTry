"""Assemble corpus v3 from the sealed family generators (SEPARATION.md). Prints aggregates only.

    python eval/v3/assemble.py --sealed /home/user/v3-sealed

Writes eval/data3/{dev3build,dev3val,roster}.jsonl + DEV_FAMILIES.json (dev side, published) and
<sealed>/out/{test3.jsonl,assignment.json} (sealed). Test family ids are never printed.
"""
import argparse, hashlib, importlib, json, random, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REG_COMMIT = "f2799b5e22a440ecb1aaf3ad93251b8afc1aef99"   # commit that registered SEPARATION.md
N_POS, N_NEG = 60, 15
ROLE = {**{i: "dev3build" for i in range(0, 4)}, **{i: "dev3val" for i in range(4, 6)}, **{i: "test3" for i in range(6, 12)}}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sealed", default="/home/user/v3-sealed")
    a = ap.parse_args()
    S = Path(a.sealed)
    sys.path.insert(0, str(S))
    from common import pools as P, roster as R          # noqa: E402  (sealed, stdlib-only package)
    from common.builder import strip_private            # noqa: E402

    cat_sha = (S / "CATALOGUE.sha256").read_text().split()[0]
    assert cat_sha == sha(S / "CATALOGUE.md"), "catalogue changed after its hash was recorded"
    seed_hex = hashlib.sha256((REG_COMMIT + cat_sha).encode()).hexdigest()[:16]
    seed = int(seed_hex, 16)
    catalogue = (S / "CATALOGUE.md").read_text(encoding="utf-8")

    mods = {}
    for p in sorted((S / "families").glob("*.py")):
        m = importlib.import_module(f"families.{p.stem}")
        mods[m.FAMILY_ID] = m
    groups = {}
    for fid, m in mods.items():
        pos = catalogue.find(fid)
        assert pos >= 0, "family module not in catalogue"
        groups.setdefault(m.GROUP, []).append((pos, fid))
    assert sorted(groups) == ["C", "I", "S"] and all(len(v) == 12 for v in groups.values()), \
        f"expected 12 families per group, got { {g: len(v) for g, v in groups.items()} }"
    split_of = {}
    for g in sorted(groups):
        order = [fid for _, fid in sorted(groups[g])]
        random.Random(f"{seed}|{g}").shuffle(order)
        for i, fid in enumerate(order):
            split_of[fid] = ROLE[i]

    roster = R.build_roster(random.Random(f"{seed}|roster"))
    tok_dev, tok_test = P.partition_external_tokens(seed=seed)
    tokens = {"dev3build": tok_dev, "dev3val": tok_dev, "test3": tok_test}
    out = {"dev3build": [], "dev3val": [], "test3": []}
    for fid in sorted(mods):            # one process: the values registry keeps identifiers unique corpus-wide
        sp = split_of[fid]
        items = mods[fid].generate(random.Random(f"{seed}|{fid}"), roster, P.POOLS, tokens[sp], N_POS, N_NEG)
        for n, it in enumerate(items):
            it = strip_private(it)
            it["split"] = sp
            it["id"] = f"{sp}-{fid}-{n:03d}"
            out[sp].append(it)

    d3 = REPO / "eval/data3"; d3.mkdir(exist_ok=True)
    so = S / "out"; so.mkdir(exist_ok=True)
    w = lambda p, rows: p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    w(d3 / "dev3build.jsonl", out["dev3build"]); w(d3 / "dev3val.jsonl", out["dev3val"])
    w(so / "test3.jsonl", out["test3"]); w(d3 / "roster.jsonl", roster); w(so / "roster.jsonl", roster)
    dev_fams = {sp: sorted(f for f, s in split_of.items() if s == sp) for sp in ("dev3build", "dev3val")}
    (d3 / "DEV_FAMILIES.json").write_text(json.dumps(dev_fams, indent=1))
    (so / "assignment.json").write_text(json.dumps({"seed_hex": seed_hex, "split_of": split_of}, indent=1))
    manifest = {"corpus": "v3", "registration_commit": REG_COMMIT, "catalogue_sha256": cat_sha, "seed_hex": seed_hex,
                "files": {n: sha(d3 / n) for n in ("dev3build.jsonl", "dev3val.jsonl", "roster.jsonl")} |
                         {"test3.jsonl": sha(so / "test3.jsonl"), "assignment.json": sha(so / "assignment.json")},
                "items": {k: len(v) for k, v in out.items()},
                "families": {k: sum(1 for s in split_of.values() if s == k) for k in out}}
    (d3 / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"seed_hex": seed_hex, "items": manifest["items"], "families": manifest["families"],
                      "dev_families": dev_fams}, indent=1))


if __name__ == "__main__":
    main()
