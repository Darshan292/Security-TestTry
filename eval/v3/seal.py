"""Seal / unseal the test side of corpus v3 (CONTAMINATION C3). AES-256-GCM over a tar archive.

    python eval/v3/seal.py seal   --src /home/user/v3-sealed --out eval/v3/sealed_v3.tar.enc --keyfile KEY
    python eval/v3/seal.py unseal --in eval/v3/sealed_v3.tar.enc --dest DIR --keyfile KEY

The key (32 random bytes, hex) is written to KEY outside the repository and given to the owner.
Plaintext tar SHA-256 is printed and recorded in FREEZE.md. Nothing inside the archive is printed.
"""
import argparse, hashlib, io, os, sys, tarfile
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

EXCLUDE = {"__pycache__"}


def seal(src: Path, out: Path, keyfile: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not (EXCLUDE & set(p.parts)):
                info = tar.gettarinfo(str(p), arcname=str(p.relative_to(src)))
                info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ""
                with open(p, "rb") as fh:
                    tar.addfile(info, fh)
    plain = buf.getvalue()
    if keyfile.exists():
        key = bytes.fromhex(keyfile.read_text().strip())
    else:
        key = os.urandom(32); keyfile.write_text(key.hex()); keyfile.chmod(0o600)
    nonce = os.urandom(12)
    out.write_bytes(b"V3S1" + nonce + AESGCM(key).encrypt(nonce, plain, b"corpus-v3"))
    print(f"plaintext tar sha256 {hashlib.sha256(plain).hexdigest()}  ({len(plain)} bytes)")
    print(f"ciphertext sha256    {hashlib.sha256(out.read_bytes()).hexdigest()}  -> {out}")


def unseal(inp: Path, dest: Path, keyfile: Path):
    blob = inp.read_bytes()
    assert blob[:4] == b"V3S1"
    plain = AESGCM(bytes.fromhex(keyfile.read_text().strip())).decrypt(blob[4:16], blob[16:], b"corpus-v3")
    print(f"plaintext tar sha256 {hashlib.sha256(plain).hexdigest()}")
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        tar.extractall(dest, filter="data")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("seal", "unseal"))
    ap.add_argument("--src"); ap.add_argument("--out"); ap.add_argument("--in", dest="inp"); ap.add_argument("--dest")
    ap.add_argument("--keyfile", required=True)
    a = ap.parse_args()
    if a.cmd == "seal":
        seal(Path(a.src), Path(a.out), Path(a.keyfile))
    else:
        unseal(Path(a.inp), Path(a.dest), Path(a.keyfile))
