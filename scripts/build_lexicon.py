"""Build pgw/detect/data/english_lexicon.txt.gz: a plain list of English words (general linguistic knowledge).

Source: the WordNet-derived lemma index and irregular-form exceptions shipped in spaCy's en_core_web_lg 3.8.0
lemmatizer lookups (WordNet 3.0 licence, see THIRD_PARTY_NOTICES.md), plus spaCy's English stop words. Only
lower-case alphabetic entries of two or more letters are kept. The file is committed so the gateway does not need
the NER model to use it; rerun this script to regenerate it (output is sorted, so it is reproducible).

    python scripts/build_lexicon.py
"""
import gzip
import re
from pathlib import Path

import en_core_web_lg
import srsly
from spacy.lang.en.stop_words import STOP_WORDS

root = Path(en_core_web_lg.__file__).parent
lk = next(root.glob("en_core_web_lg-*/lemmatizer/lookups/lookups.bin"))
d = srsly.msgpack_loads(lk.read_bytes())
words: set[str] = set()
for v in d["lemma_index"].values():
    words.update(v)
for table in d["lemma_exc"].values():
    for k, forms in table.items():
        words.add(k)
        words.update(forms)
words.update(STOP_WORDS)
keep = sorted(w for w in words if re.fullmatch(r"[a-z]{2,}", w))
out = Path(__file__).resolve().parents[1] / "pgw/detect/data/english_lexicon.txt.gz"
with gzip.GzipFile(out, "wb", mtime=0) as f:
    f.write("\n".join(keep).encode())
print(len(keep), "words ->", out)
