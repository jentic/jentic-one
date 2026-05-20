## Testing

- **Backend**: pytest-based, exercises the API at the HTTP boundary. Uses a real temp SQLite DB with Alembic migrations — no mocking. Tests organized by trust boundary (auth, policy, vault, broker, toolkit). CI: `ci-backend.yml` (path-filtered to `src/`, `tests/`, `alembic/`).
- **UI**: Vitest browser mode + MSW (`msw/browser`) + axe-core + Testing Library. See `ui/TESTING.md` for the full contributor guide. CI: `ci-ui.yml` (path-filtered) + `ci-docker.yml` (always runs).

### When to run

Run tests when your change could affect behavior covered by a suite. Skip them for pure docs, comments, or config that no test touches.

- Changed `src/`, `tests/`, or `alembic/` → run backend tests.
- Changed `ui/src/**/*.{ts,tsx}` (non-test) → run UI tests.
- Changed only test files → run just the affected suite.
- Changed both → run both.

If you cannot tell whether a change is behavior-affecting, run the relevant suite.

E2E (`test:e2e*`) is opt-in — run only when a user journey or routing change warrants it, not as part of the default UI suite.

### Commands

See @DEVELOPMENT.md ("Running Tests") for the full list of backend (`uv run poe test …`) and UI (`npm run test:run`, `npm run test:e2e`, …) commands. Extra UI targets like `npm run test:coverage` and `npm run test:e2e:ui` are documented in `ui/TESTING.md`.

In agent contexts, prefer `npm run test:run` over `npm test` — `npm test` is Vitest watch mode and does not exit.