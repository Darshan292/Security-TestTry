"""Label decoding for token classifiers: BIO / BILOU / IO tags -> entity runs. Pure Python."""
from __future__ import annotations

_BEGIN = {"B", "U", "S"}
_INSIDE = {"I", "L", "E"}
_END = {"L", "E", "U", "S"}


def split_tag(label: str) -> tuple[str, str]:
    """'B-PER' -> ('B', 'PER'); 'O' -> ('O', ''); 'PER' (IO scheme) -> ('I', 'PER')."""
    if label == "O" or not label:
        return "O", ""
    if len(label) > 2 and label[1] == "-" and label[0] in _BEGIN | _INSIDE:
        return label[0], label[2:]
    return "I", label


def decode(tags: list[str]) -> list[tuple[int, int, str]]:
    """Entity runs (first unit, last unit inclusive, type) from per-unit tags.

    Lenient, as models make boundary errors: an I/L tag that does not continue an open entity of the same type starts
    a new one (it is never dropped: a dropped tag is a leaked token). B/U/S always start a new entity; L/E/U/S close
    it after the unit."""
    out: list[tuple[int, int, str]] = []
    cur: list | None = None           # [start, end, type]
    for i, lab in enumerate(tags):
        p, t = split_tag(lab)
        if p == "O":
            if cur:
                out.append(tuple(cur))
                cur = None
            continue
        if p in _BEGIN or cur is None or cur[2] != t:
            if cur:
                out.append(tuple(cur))
            cur = [i, i, t]
        else:
            cur[1] = i
        if p in _END:
            out.append(tuple(cur))
            cur = None
    if cur:
        out.append(tuple(cur))
    return out


def viterbi_labels(logp, id2label: list[str], biases: dict, first: bool = True, last: bool = True) -> list[int]:
    """Constrained Viterbi over a window's log-probabilities ``logp`` [T, L] (numpy) for a BIOES label set.

    Allowed transitions: O->O|B|S, B|I->I|E (same type), E|S->O|B|S. A sequence may start in O, B or S and end in O, E
    or S; a window that does not begin (``first`` False) or end (``last`` False) the text may start/end anywhere. The
    six ``biases`` are added to the matching transitions (zero = maximum-likelihood valid path). Returns label ids."""
    import numpy as np
    T, L = logp.shape
    kinds = [split_tag(x) for x in id2label]
    NEG = -1e30
    trans = np.full((L, L), NEG, dtype=np.float64)
    for i, (pi, ti) in enumerate(kinds):
        for j, (pj, tj) in enumerate(kinds):
            if pi in ("O", "E", "S"):
                if pj == "O":
                    trans[i, j] = biases["transition_bias_background_stay"] if pi == "O" else \
                        biases["transition_bias_end_to_background"]
                elif pj in ("B", "S"):
                    trans[i, j] = biases["transition_bias_background_to_start"] if pi == "O" else \
                        biases["transition_bias_end_to_start"]
            elif pi in ("B", "I") and pj in ("I", "E") and ti == tj:
                trans[i, j] = biases["transition_bias_inside_to_continue"] if pj == "I" else \
                    biases["transition_bias_inside_to_end"]
    start = np.array([0.0 if (not first or k[0] in ("O", "B", "S")) else NEG for k in kinds])
    end = np.array([0.0 if (not last or k[0] in ("O", "E", "S")) else NEG for k in kinds])
    score = start + logp[0]
    back = np.zeros((T, L), dtype=np.int64)
    for t in range(1, T):
        cand = score[:, None] + trans
        back[t] = cand.argmax(axis=0)
        score = cand[back[t], np.arange(L)] + logp[t]
    score = score + end
    cur = int(score.argmax())
    out = [cur]
    for t in range(T - 1, 0, -1):
        cur = int(back[t][cur])
        out.append(cur)
    return out[::-1]
