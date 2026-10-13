"""Ablation switches: defaults are all ON and each switch visibly changes detections on a crafted input."""
import dataclasses

import pytest

from pgw import ablation
from pgw.ablation import AblationConfig
from pgw.detect.edm import RosterIndex
from pgw.pipeline import Keys, Protector
from pgw.policy import PolicyPack
from pgw.types import Context, Detection, EntityType

CTX = Context("t", "t", "eval_tokenize_all", conversation_id=None, restore_output=False)
POLICY = "policies/hris-default.yaml"


def make(roster=False, ner=None):
    keys = Keys(bytes(32))
    r = None
    if roster:
        r = RosterIndex(keys.derive("eval", "edm"))
        r.add_employee({"employee_id": "E0060982", "full_name": "Sonal Gupta", "first_name": "Sonal",
                        "last_name": "Gupta", "email": "sgupta@kestrelsys.in", "phone": "+91 76395 28404"})
    return Protector(PolicyPack.load(POLICY), keys, r, ner=ner)


def spans(prot, text):
    return {(d.start, d.end, d.type.value) for d in prot.analyze([{"role": "user", "content": text}], CTX)[0]}


def with_cfg(prot, text, **off):
    with ablation.use(AblationConfig().without(*[k for k, v in off.items() if v])):
        return spans(prot, text)


class FakeNer:
    def detect(self, text):
        i = text.find("Zorblax Quimby")
        return [Detection(i, i + 14, EntityType.PERSON, 0.75, "D4:ner:fake")] if i >= 0 else []


def test_defaults_all_on_and_frozen():
    cfg = ablation.DEFAULT
    assert all(getattr(cfg, f.name) is True for f in dataclasses.fields(cfg))
    assert {f.name for f in dataclasses.fields(cfg)} == {"D0", "D1", "D2", "CUES", "D3", "D4", "D5"}
    assert ablation.get() == AblationConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.D0 = False


def test_use_restores():
    with ablation.use(AblationConfig(D0=False)):
        assert not ablation.get().D0
    assert ablation.get() == ablation.DEFAULT


def test_d0_off_loses_spaced_value():
    p = make()
    t = "email john dot smith at example dot com"   # only the de-obfuscated view can see this address
    assert with_cfg(p, t) and not with_cfg(p, t, D0=True)


def test_d1_off_loses_field_value():
    p = make()
    t = '{"employee_name": "Quentin Blorptan"}'
    assert with_cfg(p, t) and not with_cfg(p, t, D1=True)


def test_d2_off_loses_pattern_but_keeps_d1():
    p = make()
    t = "mail me at foo.bar@example.com please"
    assert with_cfg(p, t) and not with_cfg(p, t, D2=True)
    t2 = '{"email": "foo.bar@example.com"}'
    assert with_cfg(p, t2, D2=True)          # D1 still finds it


def test_cues_off():
    p = make()
    t = "Dear Quentin Blorptan, hello"
    assert with_cfg(p, t) and not with_cfg(p, t, CUES=True)


def test_d3_off_roster_not_consulted():
    p = make(roster=True)
    t = "sonal gupta was here"
    assert with_cfg(p, t) and not with_cfg(p, t, D3=True)


def test_d4_off_no_ner():
    p = make(ner=FakeNer())
    t = "yesterday Zorblax Quimby left"
    assert with_cfg(p, t) and not with_cfg(p, t, D4=True)


def test_d5_off_no_propagation():
    p = make()
    t = "Dear Quentin Blorptan, hello. Later quentin blorptan and Quentin again."
    on, off = with_cfg(p, t), with_cfg(p, t, D5=True)
    assert len(on) > len(off) >= 1



def test_scoped_ablation_is_isolated_between_concurrent_threads():
    """PD13 scopes D1 off; concurrent requests must not inherit that temporary setting."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def observe(d1_enabled):
        cfg = AblationConfig(D1=d1_enabled)
        with ablation.use(cfg):
            barrier.wait(timeout=5)
            observed = ablation.get().D1
            barrier.wait(timeout=5)
            return observed

    with ThreadPoolExecutor(max_workers=2) as pool:
        disabled = pool.submit(observe, False)
        enabled = pool.submit(observe, True)
        assert disabled.result(timeout=10) is False
        assert enabled.result(timeout=10) is True


def test_nested_scoped_ablation_restores_outer_context():
    outer = AblationConfig(D0=False, D1=True)
    inner = AblationConfig(D0=True, D1=False)
    with ablation.use(outer):
        assert ablation.get() == outer
        with ablation.use(inner):
            assert ablation.get() == inner
        assert ablation.get() == outer
    assert ablation.get() == ablation.DEFAULT
