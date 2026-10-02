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
