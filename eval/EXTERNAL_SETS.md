# External, sealed evaluation sets (held by the external tester, never by the team)

The team and its agents never receive, open or store these sets. Only their fingerprints are recorded here, so a
later change to a set is detectable. Results are reported by the tester.

| Set | Size | Held by | sha256 |
|---|---|---|---|
| C | 40 free-text cases | external tester | (not published) |
| D | 30 new-genre cases | external tester | (not published) |
| E | 273 cases (231 identifier, 28 special-category, 14 singling-out) + 40 benign texts | external tester | `7907cb4e7c89a171697699aae00cc84e4940f64089685f30dc5e83189838b62b` |

Baseline without an ML detector (feature/oss-adoption, 2026-10-31, tester's run): E 88/231 identifier cases leak
(38%), 28/28 special-category cases cross with the sensitive detail, 14/14 singling-out cases leak, 9/40 benign texts
over-masked; C 14/40, D 13/30, B 6/40, A 0/36.
