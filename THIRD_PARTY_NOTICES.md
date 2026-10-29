# Third-party notices

## ECC (Everything Claude Code), MIT License
Source: https://github.com/affaan-m/ECC, commit `ef648e01899ba3e8dc6371642deaaf64b4477775` (reviewed 2026-10-02).
Copyright (c) 2026 Affaan Mustafa. Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files, to deal in the Software without restriction, subject to the
conditions of the MIT License (full text in the source repository's LICENSE).

Vendored (prompt files only, no executable code):
- `.claude/agents/pr-test-analyzer.md`: verbatim.
- `.claude/agents/silent-failure-hunter.md`: verbatim plus a "Project context" section added here.
- `.claude/skills/contract-first/SKILL.md`: verbatim (for the future data-contract layer, PRODUCT_ARCHITECTURE §3-E).

Deliberately **not** installed:
- ECC's hooks: about 24 Node hooks on every tool call and session event, plus memory persistence. They conflict with
  the project rule "no research in Claude memory" and are a standing supply-chain risk.
- ECC's MCP server config.
- The remaining ~290 skills and ~65 agents, which are mostly other stacks (JS/web, mobile, JVM).

## WordNet 3.0 (word list only), WordNet 3.0 License; spaCy, MIT License
`pgw/detect/data/english_lexicon.txt.gz` is a sorted list of lower-case English words derived by
`scripts/build_lexicon.py` from the WordNet lemma index and irregular forms shipped in spaCy's `en_core_web_lg` 3.8.0
lemmatizer lookups, plus spaCy's English stop words. WordNet 3.0 Copyright 2006 by Princeton University; used under
the WordNet 3.0 license (permission to use, copy, modify and distribute with this notice; THE SOFTWARE AND DATABASE
IS PROVIDED "AS IS" AND PRINCETON UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES). spaCy Copyright (C) 2016-2024
ExplosionAI GmbH, MIT License. The list holds no personal data; it is used only to tell ordinary words from name
words (pgw.detect.lexicon).

## ML extra (requirements/ml.txt, dev.txt), installed packages, not vendored

ONNX Runtime (MIT License, Microsoft), Hugging Face `tokenizers` and `huggingface-hub` (Apache License 2.0),
NumPy (BSD 3-Clause), `onnx` (Apache License 2.0; tests only). No model weights are included in this repository;
each model artefact carries its own licence in its MODEL_CARD.md and must be commercial-friendly
(docs/ML_ARCHITECTURE.md). The `llama-server` container image (llama.cpp, MIT License) is referenced by pinned
digest in deploy/docker-compose.yml, not redistributed.

## Plugins extra (requirements/plugins.txt), installed packages, not vendored

Optional; every plugin is off unless the signed deployment profile turns it on (docs/PLUGINS.md). Licences were read
from each wheel's METADATA (2026-10-28), not assumed:

| Distribution | Version | Licence (METADATA) | Role |
|---|---|---|---|
| python-stdnum | 2.2 | LGPL-2.1-or-later (`License: LGPL`, classifier "GNU Lesser General Public License v2 or later (LGPLv2+)") | validator adapter |
| phonenumbers | 9.0.42 | Apache-2.0 (`License-Expression: Apache-2.0`) | validator adapter |
| detect-secrets | 1.5.0 | Apache-2.0 (classifier "Apache Software License"; `License: UNKNOWN` field) | recognizer adapter |
| requests | 2.34.2 | Apache-2.0 | dependency of detect-secrets; imported by it at module import, never called by pgw |
| urllib3 | 2.8.0 | MIT | dependency of requests |
| charset-normalizer | 3.5.2 | MIT | dependency of requests |
| idna | 3.20 | BSD-3-Clause | dependency of requests (already in runtime.txt) |
| certifi | 2026.7.22 | MPL-2.0 | dependency of requests (already in runtime.txt via httpx; file-level copyleft, unmodified) |
| PyYAML | 6.0.3 | MIT | dependency of detect-secrets (already in runtime.txt) |

**python-stdnum is LGPL-2.1-or-later.** It is used as an unmodified library, installed from PyPI as a separate package
and imported at run time; none of its code is copied into this repository, and users can replace it with any other
version (LGPL-2.1 §6). If it is ever modified or vendored, its LGPL obligations (source of the modified library,
notice) apply to that copy. Copyright (C) Arthur de Jong and others; source: https://arthurdejong.org/python-stdnum/.
phonenumbers: Copyright (C) 2009-2024 The Libphonenumber Authors and David Drysdale (Python port), Apache-2.0.
detect-secrets: Copyright (c) 2018 Yelp, Inc., Apache-2.0.
