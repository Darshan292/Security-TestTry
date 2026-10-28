from ml.train import labels as L

import pgw.types as T
from pgw.product import contracts


def test_entity_types_equal_gateway_enum_minus_documented_exclusions():
    enum_names = {m.name for m in T.EntityType}
    expected = enum_names - set(L.EXCLUDED_GATEWAY_TYPES)
    assert set(L.ENTITY_TYPES) == expected
    assert len(L.ENTITY_TYPES) == len(set(L.ENTITY_TYPES))
    assert set(L.EXCLUDED_GATEWAY_TYPES) <= enum_names  # the exclusion list itself cannot go stale


def test_every_class_of_contracts_covers_the_labels():
    covered = {t.name for t in contracts.CLASS_OF}
    assert set(L.ENTITY_TYPES) <= covered


def test_bio_label_set():
    assert L.BIO_LABELS[0] == "O" and len(L.BIO_LABELS) == 1 + 2 * len(L.ENTITY_TYPES)
    assert all(L.ID2LABEL[L.LABEL2ID[l]] == l for l in L.BIO_LABELS)


def test_gate_mapping_names_exist_in_contracts():
    assert set(L.GATE_TO_PURPOSE_DEPENDENT.values()) <= set(contracts.PURPOSE_DEPENDENT)
    assert set(L.GATE_TO_PURPOSE_DEPENDENT) <= set(L.GATE_LABELS)
    assert "NONE" in L.GATE_LABELS and len(L.GATE_LABELS) == 14
