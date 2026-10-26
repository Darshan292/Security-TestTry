"""Test configuration helpers.

production(): a throwaway registry inside a sealed, Ed25519-signed copy of the repo configuration, so gateway tests
run in PRODUCTION mode (signed manifest, mandatory contracts) instead of switching integrity off.
legacy_dev(): the ONLY way tests reach the pre-contract path: explicit development mode + legacy flag + no manifest.
attest(): the signed source-attestation headers (X-PGW-Input-Source/-Timestamp/-Signature) for a request body.

Source keys: the repo contracts pin DEMO source keys, which production refuses. production() therefore rewrites
every demo key in the contracts it places to this test run's own source key (source_key(); generated per run, never
a demo key), and attest() signs with that key. Development-mode tests that serve the repo contracts unchanged sign
with the committed demo keys: attest(..., demo=True). production() also gives a deployment whose contracts route
unattested input to the in-boundary model (unattested: PRIVATE) a mock private endpoint, unless told otherwise.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from pgw.gateway.upstream import MockLLMUpstream
from pgw.product import attest as _attest
from pgw.product import integrity

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def signing_key() -> tuple[bytes, str]:
    return integrity.generate_signing_key()


@lru_cache(maxsize=1)
def source_key() -> tuple[str, str]:
    """(private hex, public hex): this run's source signing key, used for every sanitizer id in production tests."""
    return _attest.generate_key()


def with_test_keys(data: bytes) -> bytes:
    """Contract bytes with every DEMO source public key replaced by this run's test source key."""
    text = data.decode("utf-8")
    for demo in _attest.DEMO_PUBKEYS:
        text = text.replace(demo, source_key()[1])
    return text.encode("utf-8")


def use_test_keys(path: Path) -> None:
    path.write_bytes(with_test_keys(path.read_bytes()))


def _place(src: Path, dest_dir: Path, transform=None) -> str:
    """Copy src into dest_dir (renaming on a content clash); return its name."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = src.read_bytes() if transform is None else transform(src.read_bytes())
    name, i = src.name, 0
    while (dest_dir / name).exists() and (dest_dir / name).read_bytes() != data:
        i += 1
        name = f"{src.stem}_{i}{src.suffix}"
    if not (dest_dir / name).exists():
        (dest_dir / name).write_bytes(data)
    return name


_AUTO = object()


def production(tmp: Path, apps: list[dict], policy: str | Path | None = None, sign: bool = True,
               roster: str | Path | None = None, demo_keys: bool = False, private_upstream=_AUTO) -> dict:
    """Settings kwargs for a production-mode gateway serving `apps` (task paths may be absolute anywhere).
    `roster` (optional) is sealed with the config, as production requires.
    demo_keys=True keeps the contracts' demo source keys (which production must refuse).
    private_upstream: by default a mock in-boundary model when a contract needs one; None for no private endpoint."""
    base = Path(tmp) / "cfg"
    for d in ("registry", "policies", "contracts", "roster"):
        (base / d).mkdir(parents=True, exist_ok=True)
    pol = Path(policy) if policy else ROOT / "policies/hris-default.yaml"
    pol_name = _place(pol, base / "policies")
    out, needs_private = [], False
    for a in apps:
        a = dict(a)
        if a.get("tasks"):
            placed = {p: _place(Path(c), base / "contracts", None if demo_keys else with_test_keys)
                      for p, c in a["tasks"].items()}
            a["tasks"] = {p: f"../contracts/{n}" for p, n in placed.items()}
            for n in placed.values():
                raw = yaml.safe_load((base / "contracts" / n).read_text())
                needs_private |= isinstance(raw, dict) and isinstance(raw.get("external_input"), dict) and \
                    str(raw["external_input"].get("unattested", "")).upper() == "PRIVATE"
        out.append(a)
    reg = base / "registry/apps.yaml"
    reg.write_text(yaml.safe_dump({"version": 1, "apps": out}, sort_keys=False))
    extra = {}
    if roster:
        extra["roster_path"] = str(base / "roster" / _place(Path(roster), base / "roster"))
    manifest = base / "policies/MANIFEST.json"
    priv, pub = signing_key()
    integrity.seal(manifest, sign_key=priv if sign else None)
    if private_upstream is _AUTO:
        private_upstream = MockLLMUpstream("summarise") if needs_private else None
    if private_upstream is not None:
        extra["private_upstream"] = private_upstream
    return {"mode": "production", "registry_path": str(reg), "policy_path": str(base / "policies" / pol_name),
            "integrity_manifest": str(manifest), "trusted_config_pubkey": pub, **extra}


def legacy_dev(registry_path: str | Path) -> dict:
    """Explicit development-only legacy (uncontracted) configuration. Never valid in production."""
    return {"mode": "development", "legacy_uncontracted": True, "integrity_manifest": None,
            "registry_path": str(registry_path)}


def attest(purpose: str, body: dict, *, contract: str | None = None, demo: bool = False, key: str | None = None,
           source: str | None = None, ts: int | None = None, sign_purpose: str | None = None) -> dict:
    """Signed source-attestation headers for `body` sent to `purpose` (repo contract `contract` or `purpose`).
    Signs as that contract's first sanitizer with this run's test key (demo=True: the committed demo key).
    key/source/ts/sign_purpose override what is signed, for negative tests."""
    p = ROOT / f"contracts/{contract or purpose}.yaml"
    if not p.exists():
        return {}
    ids = sorted((yaml.safe_load(p.read_text()).get("external_input") or {}).get("sanitizers") or {})
    if not ids:
        return {}
    sid = source or ids[0]
    priv = key or (_attest.load_demo_keys()[ids[0]] if demo else source_key()[0])
    return _attest.sign(priv, sign_purpose or purpose, sid, body.get("messages"), body.get("pgw_subjects"), ts)


def sha(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()
