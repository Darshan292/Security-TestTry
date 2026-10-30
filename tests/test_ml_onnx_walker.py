"""External-data detection: the dependency-free protobuf walker (pgw.ml.onnx_util) against onnx's own helpers, and the
behaviour of the real onnxruntime on hostile models (pinned, so an ORT upgrade that changes it is noticed)."""
from __future__ import annotations

import itertools

import pytest

pytest.importorskip("onnxruntime")
onnx = pytest.importorskip("onnx")
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from onnx import TensorProto as T  # noqa: E402
from onnx import helper as h  # noqa: E402
from onnx import numpy_helper as nh  # noqa: E402
from onnx.external_data_helper import _get_all_tensors, uses_external_data  # noqa: E402,PLC2701

from pgw.ml.artifact import ArtifactError  # noqa: E402
from pgw.ml.onnx_util import make_session, reject_external_data  # noqa: E402

OPS = [h.make_opsetid("", 18), h.make_opsetid("local", 1)]
X = h.make_tensor_value_info("x", T.FLOAT, [1])
Y = h.make_tensor_value_info("y", T.FLOAT, [1])
COND = h.make_tensor_value_info("cond", T.BOOL, [])


def _tensor(name="c", external=False):
    t = h.make_tensor(name, T.FLOAT, [1], [1.0])
    if external:
        t.ClearField("float_data")
        t.data_location = T.EXTERNAL
        for k, v in (("location", "w.bin"), ("offset", "0"), ("length", "4")):
            e = t.external_data.add()
            e.key, e.value = k, v
    return t


def _sparse(external):
    vals = _tensor("v", external)
    idx = h.make_tensor("i", T.INT64, [1], [0])
    return h.make_sparse_tensor(vals, idx, [1])


def _const(out, ext):
    return h.make_node("Constant", [], [out], value=_tensor(out, ext))


def _sub(out_name, ext):
    """A subgraph returning a float [1] from a Constant."""
    o = h.make_tensor_value_info(out_name, T.FLOAT, [1])
    return h.make_graph([_const(out_name, ext)], "sub", [], [o])


def _model(nodes, inits=(), funcs=(), sparse=()):
    g = h.make_graph(nodes, "g", [X, COND], [Y], list(inits), sparse_initializer=list(sparse))
    m = h.make_model(g, opset_imports=OPS, functions=list(funcs))
    m.ir_version = 9
    return m


def _func(ext, attr_default=False):
    nodes = [_const("w", False if attr_default else ext), h.make_node("Add", ["a", "w"], ["b"])]
    f = h.make_function("local", "F", ["a"], ["b"], nodes, [h.make_opsetid("", 18)])
    if attr_default:
        f.attribute_proto.append(h.make_attribute("val", _tensor("d", ext)))
    return f


def _case(where, ext):
    """(model, has_external) for one placement of a tensor (external or inline)."""
    add = h.make_node("Add", ["x", "w"], ["y"])
    if where == "initializer":
        return _model([add], [nh.from_array(np.array([2.0], np.float32), "w") if not ext else _tensor("w", True)])
    if where == "constant":
        return _model([_const("w", ext), add])
    if where == "sparse_initializer":
        return _model([h.make_node("Identity", ["x"], ["y"])], sparse=[_sparse(ext)])
    if where == "tensors_list":
        n = h.make_node("Identity", ["x"], ["y"])
        n.attribute.append(h.make_attribute("ts", [_tensor("a", False), _tensor("b", ext)]))
        return _model([n])
    if where == "sparse_attr":
        n = h.make_node("Identity", ["x"], ["y"])
        n.attribute.append(h.make_attribute("sp", _sparse(ext)))
        return _model([n])
    if where == "sparse_list_attr":
        n = h.make_node("Identity", ["x"], ["y"])
        n.attribute.append(h.make_attribute("sps", [_sparse(False), _sparse(ext)]))
        return _model([n])
    if where == "if":
        n = h.make_node("If", ["cond"], ["y"], then_branch=_sub("t", ext), else_branch=_sub("e", False))
        return _model([n])
    if where == "if_nested":
        inner = h.make_graph([h.make_node("If", ["cond"], ["t"], then_branch=_sub("a", ext),
                                          else_branch=_sub("b", False))], "mid", [], [h.make_tensor_value_info("t", T.FLOAT, [1])])
        n = h.make_node("If", ["cond"], ["y"], then_branch=inner, else_branch=_sub("e", False))
        return _model([n])
    if where == "loop":
        body = h.make_graph([_const("w", ext), h.make_node("Identity", ["c_in"], ["c_out"])], "body",
                            [h.make_tensor_value_info("i", T.INT64, []), h.make_tensor_value_info("c_in", T.BOOL, [])],
                            [h.make_tensor_value_info("c_out", T.BOOL, []), h.make_tensor_value_info("w", T.FLOAT, [1])])
        n = h.make_node("Loop", ["", "cond"], ["y"], body=body)
        return _model([n])
    if where == "function_constant":
        return _model([h.make_node("F", ["x"], ["y"], domain="local")], funcs=[_func(ext)])
    if where == "function_attr_default":
        return _model([h.make_node("F", ["x"], ["y"], domain="local")], funcs=[_func(ext, attr_default=True)])
    if where == "function_subgraph":
        n = h.make_node("If", ["cond"], ["b"], then_branch=_sub("t", ext), else_branch=_sub("e", False))
        f = h.make_function("local", "F", ["a", "cond"], ["b"], [n], [h.make_opsetid("", 18)])
        return _model([h.make_node("F", ["x", "cond"], ["y"], domain="local")], funcs=[f])
    raise AssertionError(where)


WHERES = ["initializer", "constant", "sparse_initializer", "tensors_list", "sparse_attr", "sparse_list_attr", "if",
          "if_nested", "loop", "function_constant", "function_attr_default", "function_subgraph"]
CASES = list(itertools.product(WHERES, [False, True]))


def _all_messages(msg):
    """Every protobuf message under ``msg``, independent of any field number."""
    yield msg
    for fd, val in msg.ListFields():
        if fd.type == fd.TYPE_MESSAGE:
            for v in (val if isinstance(val, (list, tuple)) or hasattr(val, "__len__") else [val]):
                yield from _all_messages(v)


def _reference(model) -> bool:
    return any(uses_external_data(m) for m in _all_messages(model) if isinstance(m, T))


def _refused(model) -> bool:
    try:
        reject_external_data(model.SerializeToString())
    except ArtifactError:
        return True
    return False


@pytest.mark.parametrize("where,ext", CASES, ids=[f"{w}-{'ext' if e else 'inline'}" for w, e in CASES])
def test_walker_matches_onnx_reference(where, ext):
    model = _case(where, ext)
    assert _reference(model) is ext, "the generated corpus does not contain what it claims"
    assert _refused(model) is ext
    if "sparse" not in where and where != "function_attr_default":  # helper skips sparse tensors and function attr defaults
        assert any(uses_external_data(t) for t in _get_all_tensors(model)) is ext


def test_onnx_helper_misses_sparse_tensors():
    """onnx's _get_all_tensors does not visit sparse tensors; the walker does (so it is not just a copy of the helper)."""
    for where in ("sparse_initializer", "sparse_attr", "sparse_list_attr"):
        model = _case(where, True)
        assert not any(uses_external_data(t) for t in _get_all_tensors(model))
        assert _refused(model)


def test_clean_model_with_local_function_loads_and_runs():
    model = _case("function_constant", False)
    assert not _refused(model)
    s = make_session(model.SerializeToString())
    out = s.run(None, {"x": np.array([1.0], np.float32), "cond": np.array(True)})[0]
    assert out.tolist() == [2.0]


def test_external_reference_in_function_constant_is_refused_by_our_check():
    with pytest.raises(ArtifactError, match="external data"):
        make_session(_case("function_constant", True).SerializeToString())


def test_walker_rejects_garbage():
    with pytest.raises(ArtifactError, match="not a valid"):
        reject_external_data(b"\xff\xff\xff")


# --- what onnxruntime itself does (1.31.1 at the time of writing) ----------------------------------------------------

def _ort_run(model, level):
    so = ort.SessionOptions()
    so.graph_optimization_level = level
    so.log_severity_level = 4
    s = ort.InferenceSession(model.SerializeToString(), so, providers=["CPUExecutionProvider"])
    return s.run(None, {"x": np.array([1.0], np.float32), "cond": np.array(True)})[0].tolist()


def _hostile(kind):
    """External reference to w.bin (= [5.0]); an unrefused load would return x + 5 = 6.0."""
    if kind == "initializer":
        return _model([h.make_node("Add", ["x", "w"], ["y"])], [_tensor("w", True)])
    if kind == "constant":
        return _model([_const("w", True), h.make_node("Add", ["x", "w"], ["y"])])
    return _model([h.make_node("F", ["x"], ["y"], domain="local")], funcs=[_func(True)])


@pytest.mark.parametrize("kind", ["initializer", "constant", "function"])
def test_ort_behaviour_on_external_data_from_bytes(kind, tmp_path, monkeypatch):
    """Pins onnxruntime's behaviour with the data file placed in the cwd. Recorded for 1.31.1:
    default optimisation (ORT_ENABLE_ALL, what make_session uses): load fails in the optimiser ("model_path must not be
    empty") for all three placements; optimisation disabled: the model LOADS and reads the cwd file. If this test fails
    after an ORT upgrade, re-read docs/ML_ARCHITECTURE.md (external data) before relaxing anything; the check in
    pgw.ml.onnx_util does not depend on either outcome."""
    (tmp_path / "w.bin").write_bytes(np.array([5.0], np.float32).tobytes())
    monkeypatch.chdir(tmp_path)
    model = _hostile(kind)
    assert _refused(model)                              # our check refuses before ORT is involved
    with pytest.raises(Exception, match="model_path must not be empty"):
        _ort_run(model, ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
    assert _ort_run(model, ort.GraphOptimizationLevel.ORT_DISABLE_ALL) == [6.0]
