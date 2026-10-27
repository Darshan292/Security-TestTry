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
