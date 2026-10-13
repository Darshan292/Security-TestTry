"""Test configuration helpers.

production(): a throwaway registry inside a sealed, Ed25519-signed copy of the repo configuration, so gateway tests
run in PRODUCTION mode (signed manifest, mandatory contracts) instead of switching integrity off.
legacy_dev(): the ONLY way tests reach the pre-contract path: explicit development mode + legacy flag + no manifest.
source(): the X-PGW-Input-Source attestation header a contracted purpose accepts.
"""
from __future__ import annotations

import hashlib
import shutil
from functools import lru_cache
from pathlib import Path

import yaml

from pgw.product import integrity
from pgw.product.attestation import sign_headers

ROOT = Path(__file__).resolve().parents[1]
_SANITIZER_IDS = ("band-review-form@1", "esi-coverage-form@1", "hris-case-export@1", "hr-email-composer@1", "comp-review-form@1", "helpdesk-ticket-export@1")
TEST_SANITIZER_KEYS = {sid: hashlib.sha256(("NOT_FOR_PRODUCTION:" + sid).encode()).digest() for sid in _SANITIZER_IDS}
class _NoopNer:
    def detect(self, text): return []
TEST_NER = _NoopNer()


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


def production(tmp: Path, apps: list[dict], policy: str | Path | None = None, sign: bool = True) -> dict:
    """Settings kwargs for a production-mode gateway serving `apps` (task paths may be absolute anywhere)."""
    base = Path(tmp) / "cfg"
    for d in ("registry", "policies", "contracts"):
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
    manifest = base / "policies/MANIFEST.json"
    priv, pub = signing_key()
    integrity.seal(manifest, sign_key=priv if sign else None)
    return {"mode": "production", "registry_path": str(reg), "policy_path": str(base / "policies" / pol_name),
            "integrity_manifest": str(manifest), "trusted_config_pubkey": pub, "sanitizer_hmac_keys": TEST_SANITIZER_KEYS, "ner_detector_override": TEST_NER}


def legacy_dev(registry_path: str | Path) -> dict:
    """Explicit development-only legacy (uncontracted) configuration. Never valid in production."""
    return {"mode": "development", "legacy_uncontracted": True, "integrity_manifest": None,
            "registry_path": str(registry_path)}


def source(purpose: str, body: dict | None = None, *, app_id="pilot", tenant="acme") -> dict:
    """Synthetic signed attestation; body-less calls remain unsigned for negative tests."""
    p = ROOT / f"contracts/{purpose}.yaml"
    if not p.exists():
        return {}
    ids = (yaml.safe_load(p.read_text()).get("external_input") or {}).get("sanitizers") or []
    if not ids or body is None: return {"X-PGW-Input-Source": ids[0]} if ids else {}
    sid = sorted(ids)[0]
    return sign_headers(sanitizer=sid, body=body, key=TEST_SANITIZER_KEYS[sid], tenant=tenant, app_id=app_id, purpose=purpose)


def sha(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()
