"""Every third-party import under pgw/ must be covered by a pinned, hashed entry in requirements/runtime.txt."""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ML_EXTRA = {"onnxruntime", "tokenizers", "numpy"}   # optional ML extra (pgw/ml): requirements/ml.txt
OPTIONAL_EXTRAS = {"spacy",            # optional NER extra: requirements/ner.txt
                   *ML_EXTRA,
                   "llama_cpp"}         # optional local-LLM backend for pgw.private_llm (not locked; not shipped)
# import name -> PyPI distribution name, where they differ
DIST = {"yaml": "pyyaml", "ahocorasick": "pyahocorasick"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _third_party_imports() -> set[str]:
    found = set()
    for path in (ROOT / "pgw").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for n in names:
                top = n.split(".")[0]
                if top not in sys.stdlib_module_names and top != "pgw" and top not in OPTIONAL_EXTRAS:
                    found.add(top)
    return found


def _locked(path: Path) -> dict[str, list[str]]:
    """name -> list of sha256 hashes, for exact `name==version` requirements."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==\S+(.*)$", line.strip())
        if m:
            out[_norm(m.group(1))] = re.findall(r"--hash=sha256:([0-9a-f]{64})", m.group(2))
    return out


def test_runtime_lock_is_fully_hashed():
    locked = _locked(ROOT / "requirements" / "runtime.txt")
    assert locked, "requirements/runtime.txt has no pinned requirements"
    assert not [n for n, h in locked.items() if not h], "unhashed pins"


def test_every_pgw_import_is_locked():
    locked = _locked(ROOT / "requirements" / "runtime.txt")
    missing = {imp: DIST.get(imp, imp) for imp in _third_party_imports()
               if _norm(DIST.get(imp, imp)) not in locked}
    assert not missing, f"imports not covered by hashed pins in requirements/runtime.txt: {missing}"


def test_ner_model_is_url_and_hash_pinned():
    text = (ROOT / "requirements" / "ner-model.txt").read_text(encoding="utf-8").replace("\\\n", " ")
    assert re.search(r"en_core_web_lg @ https://\S+en_core_web_lg-3\.8\.0\S*\.whl\s+--hash=sha256:[0-9a-f]{64}", text)


def test_ml_extra_is_hashed_and_covers_every_pgw_ml_import():
    locked = _locked(ROOT / "requirements" / "ml.txt")
    assert locked and not [n for n, h in locked.items() if not h], "unhashed pins in requirements/ml.txt"
    assert {_norm(m) for m in ML_EXTRA} <= set(locked)
    runtime = _locked(ROOT / "requirements" / "runtime.txt")
    shared = set(locked) & set(runtime)        # compiled with -c runtime.txt: identical pins
    text = lambda p: (ROOT / "requirements" / p).read_text(encoding="utf-8")  # noqa: E731
    for n in shared:
        v = re.search(rf"(?im)^{re.escape(n)}==(\S+)", text("ml.txt").replace("_", "-"))
        w = re.search(rf"(?im)^{re.escape(n)}==(\S+)", text("runtime.txt").replace("_", "-"))
        assert v and w and v.group(1) == w.group(1), n
