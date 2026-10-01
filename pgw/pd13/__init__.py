"""PD-13 minimum experiment (PD13_DESIGN.md): L0+ views, L1 record parsing, L2 shape candidates, L3 field
semantics (arm B only), L4 release policy. ``build_protector(data, ner=..., l3=bool)``: B is l3=True, C l3=False."""
from .protector import PD13Protector, build_protector

__all__ = ["PD13Protector", "build_protector"]
