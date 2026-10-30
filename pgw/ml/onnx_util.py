"""ONNX Runtime (CPU) and `tokenizers` helpers shared by the backends. Imports are lazy (optional extra)."""
from __future__ import annotations

from .artifact import Artifact, ArtifactError

_INT_TYPES = {"tensor(int64)": "int64", "tensor(int32)": "int32"}


# Minimal protobuf wire walker (no `onnx` dependency at runtime) to find external-data tensors. ONNX field numbers:
# (verified against onnx/onnx.proto, pinned by tests/test_ml_onnx_walker.py):
# ModelProto.graph=7, .functions=25; GraphProto.node=1, .initializer=5, .sparse_initializer=15;
# SparseTensorProto.values=1, .indices=2; NodeProto.attribute=5;
# AttributeProto.t=5, .g=6, .tensors=10, .graphs=11, .sparse_tensor=22, .sparse_tensors=23;
# FunctionProto.node=7, .attribute_proto=11 (attribute defaults); TensorProto.external_data=13, .data_location=14 (1 = EXTERNAL).
_CHILDREN = {"model": {7: "graph", 25: "function"}, "function": {7: "node", 11: "attr"},
             "graph": {1: "node", 5: "tensor", 15: "sparse"}, "sparse": {1: "tensor", 2: "tensor"},
             "node": {5: "attr"}, "attr": {5: "tensor", 6: "graph", 10: "tensor", 11: "graph", 22: "sparse", 23: "sparse"},
             "tensor": {}}


def _varint(buf, i: int) -> tuple[int, int]:
    v = shift = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if not b & 0x80:
            return v, i
        shift += 7


def _has_external(buf, kind: str) -> bool:
    i, n = 0, len(buf)
    kids = _CHILDREN[kind]
    while i < n:
        key, i = _varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
            if kind == "tensor" and field == 14 and v == 1:
                return True
        elif wt == 2:
            ln, i = _varint(buf, i)
            if i + ln > n:
                raise ValueError("truncated field")
            if kind == "tensor" and field == 13:
                return True
            if field in kids and _has_external(buf[i:i + ln], kids[field]):
                return True
            i += ln
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            raise ValueError("unsupported wire type")
    return False


def reject_external_data(model_bytes: bytes) -> None:
    """Refuse a model whose tensors point at external data files: weights must be inline (repack with
    scripts/ml/repack_onnx.py), so the manifest digest covers every weight byte. Defence in depth: onnxruntime 1.31.1
    already refuses to resolve external data for a model built from bytes (pinned by tests/test_ml_ort_external.py),
    but this check keeps the property independent of the ORT version."""
    try:
        found = _has_external(memoryview(model_bytes), "model")
    except (ValueError, IndexError):
        raise ArtifactError("model.onnx is not a valid ONNX protobuf") from None
    if found:
        raise ArtifactError("model.onnx references external data files: weights must be inline "
                            "(repack with scripts/ml/repack_onnx.py)")


def make_session(model_bytes: bytes, intra_op_threads: int = 1, inter_op_threads: int = 1):
    """A CPU-only inference session built from the verified in-memory model bytes."""
    import onnxruntime as ort
    reject_external_data(model_bytes)
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(intra_op_threads)
    so.inter_op_num_threads = int(inter_op_threads)
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    # Idle worker threads must not spin: several workers share the cores of one host.
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    try:
        return ort.InferenceSession(model_bytes, sess_options=so, providers=["CPUExecutionProvider"])
    except Exception as e:  # noqa: BLE001 - any load failure is an artefact problem; startup stops
        raise ArtifactError(f"model.onnx does not load: {type(e).__name__}") from None


def load_tokenizer(art: Artifact):
    from tokenizers import Tokenizer
    try:
        tok = Tokenizer.from_str(art.files["tokenizer.json"].decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ArtifactError(f"tokenizer.json does not load: {type(e).__name__}") from None
    tok.no_truncation()       # windows are built here; never silently cut text
    tok.no_padding()
    return tok


def special_ids(tok, doc: dict) -> tuple[int | None, int | None, int]:
    """(cls id, sep id, pad id) from labels.json special_tokens (absent -> no cls/sep, pad 0)."""
    sp = doc.get("special_tokens") or {}
    out = []
    for k in ("cls", "sep", "pad"):
        if k in sp:
            i = tok.token_to_id(sp[k])
            if i is None:
                raise ArtifactError(f"labels.json special token {k!r} is not in the tokenizer vocabulary")
            out.append(i)
        else:
            out.append(None)
    return out[0], out[1], out[2] if out[2] is not None else 0


def input_spec(session, allowed: dict[str, str]) -> dict[str, str]:
    """{input name: numpy dtype}. Every model input must be one this backend knows how to feed."""
    spec = {}
    for i in session.get_inputs():
        if i.name not in allowed:
            raise ArtifactError(f"model.onnx has an input this backend cannot feed: {i.name!r}")
        want = allowed[i.name]
        if want == "int" and i.type not in _INT_TYPES:
            raise ArtifactError(f"model.onnx input {i.name!r} must be int64 or int32")
        spec[i.name] = _INT_TYPES.get(i.type, "bool" if i.type == "tensor(bool)" else "int64")
    return spec


def windows(n: int, size: int, stride: int) -> list[tuple[int, int]]:
    """Token windows [a, b) of at most `size` tokens that overlap by `stride` tokens and cover [0, n)."""
    if n <= 0:
        return [(0, 0)]
    if n <= size:
        return [(0, n)]
    step = max(1, size - stride)
    out, a = [], 0
    while True:
        b = min(a + size, n)
        out.append((a, b))
        if b >= n:
            return out
        a += step


def softmax(x):
    import numpy as np
    z = x - x.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def sigmoid(x):
    import numpy as np
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))
