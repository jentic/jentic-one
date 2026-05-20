## Parallel sessions via worktrees

When the user asks for a worktree:

1. Create it with `EnterWorktree`.
2. Install deps (run in parallel):
   - **Python**: `uv sync` at the worktree root. uv always uses the
     project-local `.venv`, so no env-var prefix is needed.
   - **UI**: `npm install` in `ui/`.
   The `.venv`, `node_modules/`, and `__pycache__/` are all gitignored, so a
   fresh worktree has none of them.
3. Pick a free port pair (probe from 8901 / 5174 upward, skipping anything
   bound).
4. Report the two launch commands with `JENTIC_INTERNAL_PORT` and
   `VITE_API_HOST` pre-filled — see the example below.

### What's shared vs. isolated

`.worktreeinclude` at the repo root is the source of truth for what's copied
into new worktrees.

- **Safe to share**: config knobs (`.env`). No upstream API credentials live
  there — those are encrypted in `data/jentic-mini.db` and protected by
  `data/vault.key`.
- **Never add `data/` to `.worktreeinclude`.** Each worktree must get its own
  SQLite DB, vault key, and toolkit/credential state — that's the point of
  running parallel sessions. Sharing `data/` would let one session corrupt
  another's state.

### Host-mode launch (one stack per worktree)

Backend + UI must agree on a port. The default worktree uses 8900 / 5173;
pick a different pair for each additional worktree. `JENTIC_INTERNAL_PORT`
is read by `src/startup.py`, `src/routers/broker.py`, and
`src/routers/workflows.py` — set it whenever the backend isn't on 8900.

One env var beyond port selection is required for host runs in a worktree:

- `DB_PATH=$(pwd)/data/jentic-mini.db` — `src/config.py` defaults `DB_PATH`
  to `/app/data/jentic-mini.db` for Docker. Host runs must override it to a
  worktree-local path or the backend crashes on startup. Run `mkdir -p data`
  first if `data/` is empty (only `.gitkeep` survives a fresh checkout).

```bash
# 1. Build static assets FIRST (gitignored, missing from a fresh worktree).
#    src/main.py mounts /static only if static/ exists at startup, and the
#    build also lays down the Swagger/ReDoc assets that /docs and /redoc
#    serve. Skip this and /docs returns 404.
(cd ui && npm run build)

# 2. Backend (from worktree root)
mkdir -p data
JENTIC_INTERNAL_PORT=8901 \
DB_PATH="$(pwd)/data/jentic-mini.db" \
  uv run uvicorn src.main:app --port 8901 --reload

# 3. UI dev server with HMR (optional, in another terminal from ui/)
VITE_API_HOST=http://localhost:8901 npm run dev -- --port 5174
```

Order matters: `npm run build` before backend launch, because the `/static`
mount is set up once at startup. If you build later, restart the backend.

### Docker mode

`compose.yml` hardcodes `container_name: jentic-mini` and host-side ports
`8900` / `5173`, so two Docker stacks can't run side-by-side without compose
changes. If the user asks for Docker mode in a worktree, stop the parent's
stack first (`docker compose down` from the parent checkout) before bringing
the worktree's up. Only one Docker stack at a time.
