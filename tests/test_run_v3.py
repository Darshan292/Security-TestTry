from eval.run_v3 import complete_exposed, norm_key, partial_identifying, _fold


def ce(v, t, out):
    return complete_exposed(v, t, out, _fold(out))


def test_verbatim_and_reformatted_digits():
    assert ce("2345 6789 0123", "AADHAAR", "id 2345 6789 0123 ok")
    assert ce("2345 6789 0123", "AADHAAR", "id 2345-6789-0123 ok")
    assert ce("2345 6789 0123", "AADHAAR", "id 234567890123")
    assert not ce("2345 6789 0123", "AADHAAR", "id 2345 6789 [AADHAAR_1ABCDE]")
    assert not ce("2345 6789 0123", "AADHAAR", "x 123456789012345 y")          # embedded in a longer digit run


def test_text_types_fold_case_and_space():
    assert ce("Priya  Nair", "PERSON", "met PRIYA NAIR today")
    assert not ce("Priya Nair", "PERSON", "met [PERSON_2ABCDE] today")
    assert ce("ABCPE1234F", "PAN", "pan abcpe1234f")


def test_partial_identifying():
    assert partial_identifying("2345 6789 0123", "AADHAAR", "x 23456789 y")
    assert not partial_identifying("2345 6789 0123", "AADHAAR", "x 0123 y")
    assert partial_identifying("s3cr3tTokenValue", "SECRET", "tok s3cr3tTo")
    assert not partial_identifying("s3cr3tTokenValue", "SECRET", "tok s3c")


def test_norm_key():
    assert norm_key("Date_of-Birth ") == norm_key("dateofbirth")
    assert norm_key("जन्म तिथि") == "जन्मतिथि"


def test_summarise_empty_kind_does_not_crash():
    from collections import defaultdict
    from eval.run_v3 import summarise
    r = summarise({}, defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int), {}, [])
    assert r["docs"] == 0 and r["csve"] is None
