---
paths:
  - "pyproject.toml"
  - "ui/package.json"
  - "Dockerfile"
---

When a change touches `pyproject.toml`, `ui/package.json`, or `Dockerfile` and either **adds a library, removes a library, or changes its role in the architecture**, update `specs/tech-stack.md` in the same commit.

Version bumps do NOT require a constitution update — `pyproject.toml` and `ui/package.json` are the version source of truth. Do not re-add version numbers to `specs/tech-stack.md`.

The bar for "belongs in `specs/tech-stack.md`" is: **would swapping this out force an architectural change?**

- **Yes (load-bearing, list in tech-stack.md):** FastAPI, React, SQLite + Alembic, Fernet, rank-bm25, arazzo-runner, Vite, TailwindCSS 4, @tanstack/react-query, react-router-dom, msw, pytest, Vitest + Playwright + axe-core, uv, Docker multi-stage.
- **No (swappable implementation detail, do NOT list):** HTTP client choices (httpx vs aiohttp), password-hash impls (bcrypt vs argon2), JWT impls (python-jose vs PyJWT), icon libraries, class-merge helpers, YAML parsers, contract-test runners, vendored asset packages.

If in doubt, ask the same question again: would replacing it with an equivalent competitor change the shape of the system? If no, leave it out of the constitution.