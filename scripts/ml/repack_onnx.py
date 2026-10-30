#!/usr/bin/env python3
"""Repack an ONNX model that ships its weights in external data files into ONE self-contained model.onnx.

The gateway's artefact format (pgw/ml/artifact.py) requires inline weights: the single model.onnx is covered by the
manifest digest and the session is built from those verified in-memory bytes, so there is no path that lets a file
swapped on disk after the check be used. A model published with `model.onnx` + `model.onnx_data*` (for example
openai/privacy-filter, onnx/model_quantized.onnx + model_quantized.onnx_data) is therefore repacked here, offline, once.
This is possible only when the result stays below the 2 GiB protobuf limit (the int8 and q4 variants do; fp32 does not).

What this script guarantees (the repack is a trust boundary, so it is strict):
  * every external-data reference must be a plain file name in the source directory (no path separators, no '..',
    not absolute), a regular file, not a symlink; offset/length must lie inside the file;
  * optional --expect NAME=SHA256 pins every source file (model and data files) BEFORE anything is parsed from it;
  * each data file is read ONCE into memory; the tensors are filled from those bytes (the file is not read again, so a
    swap between hash and load cannot matter);
  * the output has no external-data references left and is below the protobuf limit, else nothing is written;
  * --parity runs the source (ORT loads it by path) and the repacked bytes on one sample and requires equal outputs.

    python scripts/ml/repack_onnx.py SRC_DIR MODEL_FILE OUT.onnx [--expect model_quantized.onnx=<sha> ...] [--parity]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path

LIMIT = 2 * 1024 ** 3 - 64 * 1024 ** 2          # keep a margin under the 2 GiB protobuf limit


class RepackError(ValueError):
    pass


def _plain_name(name: str) -> str:
    if not name or name != os.path.basename(name) or name in (".", "..") or "\\" in name or "\x00" in name:
        raise RepackError(f"external data location {name!r} is not a plain file name")
    return name


def _read_regular(path: Path) -> bytes:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise RepackError(f"{path.name}: not a regular file (symlinks are refused)")
    with open(path, "rb") as fh:
        return fh.read()


def repack(src_dir: str | Path, model_file: str, expect: dict[str, str] | None = None) -> bytes:
    """Serialized self-contained ModelProto bytes. See the module docstring for the guarantees."""
    import onnx
    from onnx.external_data_helper import ExternalDataInfo, _get_all_tensors  # noqa: PLC2701 - stable helper
    src = Path(src_dir)
    raw = _read_regular(src / _plain_name(model_file))
    got = {model_file: hashlib.sha256(raw).hexdigest()}
    model = onnx.ModelProto()
    model.ParseFromString(raw)
    ext = [t for t in _get_all_tensors(model) if t.data_location == onnx.TensorProto.EXTERNAL]
    buffers: dict[str, bytes] = {}
    for t in ext:
        name = _plain_name(ExternalDataInfo(t).location)
        if name not in buffers:
            buffers[name] = _read_regular(src / name)
            got[name] = hashlib.sha256(buffers[name]).hexdigest()
    for name, want in (expect or {}).items():
        if name not in got:
            raise RepackError(f"--expect names {name!r}, which the model does not reference")
        if got[name] != want.lower():
            raise RepackError(f"sha256 of {name} does not match the pinned value (got {got[name]})")
    unpinned = sorted(set(got) - set(expect or {})) if expect else []
    if unpinned:
        raise RepackError(f"files read but not pinned by --expect: {unpinned}")
    for t in ext:
        info = ExternalDataInfo(t)
        buf = buffers[info.location]
        off = int(info.offset or 0)
        ln = int(info.length) if info.length else len(buf) - off
        if off < 0 or ln < 0 or off + ln > len(buf):
            raise RepackError(f"tensor {t.name!r}: external data range is outside {info.location}")
        t.raw_data = buf[off:off + ln]
        del t.external_data[:]
        t.data_location = onnx.TensorProto.DEFAULT
    left = [t.name for t in _get_all_tensors(model) if t.data_location == onnx.TensorProto.EXTERNAL]
    if left:
        raise RepackError(f"external references remain: {left[:3]}")
    size = model.ByteSize()
    if size > LIMIT:
        raise RepackError(f"repacked model is {size} bytes: over the 2 GiB protobuf limit; cannot be inline "
                          "(use the int8 or q4 variant)")
    return model.SerializeToString()


def parity(src_dir: Path, model_file: str, packed: bytes, text: str = "Priya Nair, priya.nair@example.com, 9876543210.") -> float:
    """Max abs difference of the first output between the external-data model (by path) and the repacked bytes."""
    import numpy as np
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    a = ort.InferenceSession(str(src_dir / model_file), so, providers=["CPUExecutionProvider"])
    b = ort.InferenceSession(packed, so, providers=["CPUExecutionProvider"])
    ids = list(range(5, 5 + 24))
    tok = src_dir / "tokenizer.json"
    if tok.exists():
        from tokenizers import Tokenizer
        ids = Tokenizer.from_file(str(tok)).encode(text).ids or ids
    feed = {}
    for i in a.get_inputs():
        dt = np.int64 if "int64" in i.type else np.int32
        feed[i.name] = (np.array([ids], dtype=dt) if i.name == "input_ids" else
                        np.ones((1, len(ids)), dtype=dt) if i.name == "attention_mask" else
                        np.arange(len(ids), dtype=dt)[None] if i.name == "position_ids" else
                        np.zeros((1, len(ids)), dtype=dt))
    ya = a.run(None, feed)[0]
    yb = b.run(None, feed)[0]
    return float(np.abs(ya - yb).max())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src_dir")
    ap.add_argument("model_file")
    ap.add_argument("out")
    ap.add_argument("--expect", action="append", default=[], metavar="NAME=SHA256")
    ap.add_argument("--parity", action="store_true")
    a = ap.parse_args(argv)
    expect = {}
    for e in a.expect:
        n, _, h = e.partition("=")
        if len(h) != 64:
            raise SystemExit("--expect needs NAME=<64 hex>")
        expect[n] = h
    try:
        packed = repack(a.src_dir, a.model_file, expect or None)
    except RepackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    out = Path(a.out)
    if out.exists():
        print(f"error: {out} exists", file=sys.stderr)
        return 2
    if a.parity:
        d = parity(Path(a.src_dir), a.model_file, packed)
        print(f"parity max abs diff: {d:.3e}")
        if d > 1e-5:
            print("error: repacked model differs from the source", file=sys.stderr)
            return 1
    out.write_bytes(packed)
    print(f"wrote {out} ({len(packed)} bytes, sha256 {hashlib.sha256(packed).hexdigest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
