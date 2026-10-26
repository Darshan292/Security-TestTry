"""Test configuration helpers.

production(): a throwaway registry inside a sealed, Ed25519-signed copy of the repo configuration, so gateway tests
run in PRODUCTION mode (signed manifest, mandatory contracts, signed deployment profile) instead of switching
integrity off. Security-relevant settings (limits, NER, egress) are passed as `deployment={...}` and flow through the
signed profile, as in a real deployment; passing them to Settings instead stops startup.
write_profile(): the deployment profile of a sealed tree (default: Settings defaults, NER off with a justification,
because loading spaCy per test is too slow; NER has dedicated tests).
legacy_dev(): the ONLY way tests reach the pre-contract path: explicit development mode + legacy flag + no manifest.
source(): the X-PGW-Input-Source attestation header a contracted purpose accepts.
"""
from __future__ import annotations

import dataclasses
import hashlib
import shutil
from functools import lru_cache
from pathlib import Path

import yaml

from pgw.gateway import settings as gw_settings
from pgw.product import integrity

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def signing_key() -> tuple[bytes, str]:
    return integrity.generate_signing_key()


def _place(src: Path, dest_dir: Path) -> str:
    """Copy src into dest_dir (renaming on a content clash); return its name."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name, i = src.name, 0
    while (dest_dir / name).exists() and (dest_dir / name).read_bytes() != src.read_bytes():
        i += 1
        name = f"{src.stem}_{i}{src.suffix}"
    if not (dest_dir / name).exists():
        shutil.copyfile(src, dest_dir / name)
    return name


TEST_NER_OFF = "test suite: NER covered by dedicated tests"


def profile_values(**overrides) -> dict:
    """Profile settings: the Settings defaults (NER off) with `overrides`. Paths are relative to the sealed tree."""
    d = {f.name: f.default for f in dataclasses.fields(gw_settings.Settings) if f.name in gw_settings.PROFILE_FIELDS}
    d.update(registry_path="registry/apps.yaml", policy_path="policies/hris-default.yaml", roster_path=None)
    d.update(overrides)
    return {k: list(v) if isinstance(v, tuple) else v for k, v in d.items()}


def write_profile(base: Path, deployment: dict | None = None) -> Path:
    """Write <base>/registry/deployment.yaml. `deployment` may override any profile setting and
    `ner_off_justification` (default: TEST_NER_OFF when NER is off)."""
    dep = dict(deployment or {})
    just = dep.pop("ner_off_justification", TEST_NER_OFF)
    vals = profile_values(**dep)
    doc = {"kind": gw_settings.PROFILE_DOC_KIND, "version": 1, "settings": vals}
    if vals["ner_model"] is None and just is not None:
        doc["ner_off_justification"] = just
    path = Path(base) / gw_settings.PROFILE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def production(tmp: Path, apps: list[dict], policy: str | Path | None = None, sign: bool = True,
               roster: str | Path | None = None, deployment: dict | None = None) -> dict:
    """Settings kwargs for a production-mode gateway serving `apps` (task paths may be absolute anywhere).
    `roster` (optional) is sealed with the config, as production requires. `deployment`: profile settings
    (e.g. {"max_body_bytes": 4096, "ner_model": "en_core_web_lg"}), sealed and signed with the rest."""
    base = Path(tmp) / "cfg"
    for d in ("registry", "policies", "contracts", "roster"):
        (base / d).mkdir(parents=True, exist_ok=True)
    pol = Path(policy) if policy else ROOT / "policies/hris-default.yaml"
    pol_name = _place(pol, base / "policies")
    out = []
    for a in apps:
        a = dict(a)
        if a.get("tasks"):
            a["tasks"] = {p: f"../contracts/{_place(Path(c), base / 'contracts')}" for p, c in a["tasks"].items()}
        out.append(a)
    reg = base / "registry/apps.yaml"
    reg.write_text(yaml.safe_dump({"version": 1, "apps": out}, sort_keys=False))
    extra = {}
    paths = {"policy_path": f"policies/{pol_name}"}
    if roster:
        rname = _place(Path(roster), base / "roster")
        extra["roster_path"] = str(base / "roster" / rname)
        paths["roster_path"] = f"roster/{rname}"
    write_profile(base, {**paths, **(deployment or {})})
    manifest = base / "policies/MANIFEST.json"
    priv, pub = signing_key()
    integrity.seal(manifest, sign_key=priv if sign else None)
    return {"mode": "production", "registry_path": str(reg), "policy_path": str(base / "policies" / pol_name),
            "integrity_manifest": str(manifest), "trusted_config_pubkey": pub, **extra}


def legacy_dev(registry_path: str | Path) -> dict:
    """Explicit development-only legacy (uncontracted) configuration. Never valid in production."""
    return {"mode": "development", "legacy_uncontracted": True, "integrity_manifest": None,
            "registry_path": str(registry_path)}


def source(purpose: str) -> dict:
    """The input-source attestation header for a repo contract (first allowlisted sanitizer)."""
    p = ROOT / f"contracts/{purpose}.yaml"
    if not p.exists():
        return {}
    ids = (yaml.safe_load(p.read_text()).get("external_input") or {}).get("sanitizers") or []
    return {"X-PGW-Input-Source": ids[0]} if ids else {}


def sha(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()
