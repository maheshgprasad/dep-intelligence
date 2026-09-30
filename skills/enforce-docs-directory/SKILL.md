---
name: enforce-docs-directory
description: Places new documentation in Docs/. Use when creating or moving markdown, guides, changelogs, or other documentation files.
---

# Documentation directory

Write documentation under `Docs/`. Subdirectories are fine.

Exceptions that stay at the repository root:

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`

If a request names another path, create the file under `Docs/` and say that you moved it. `Docs/spec/` is the original requirements set. New guides go beside it, not inside it, unless the user is editing the spec.
