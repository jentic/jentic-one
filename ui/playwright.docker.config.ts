import { defineConfig } from '@playwright/test';
import { STORAGE_STATE_PATH } from './e2e/docker/helpers';

/**
 * Real-backend e2e config. Runs specs under e2e/docker/ against the combined
 * jentic-one app serving the SPA same-origin on :8000 (`make start-app`), backed
 * by Postgres fixtures (`make start-fixtures`). Contrast playwright.config.ts,
 * which boots Vite + MSW with no backend.
 *
 * Boot recipe (clean DB so the first-run, no-credential setup gate is present):
 *   make destroy-fixtures && make start-fixtures && make start-app
 *   cd ui && npm run e2e:docker
 *
 * Auth is established ONCE via setup projects and reused via storageState — the
 * Playwright-recommended pattern (https://playwright.dev/docs/auth):
 *   - `bootstrap` clears the first-run gate by creating the first admin via /setup.
 *   - `auth` logs in and writes the browser storageState (JWT in localStorage).
 *   - `e2e` runs every other spec with that storageState, so specs start
 *     already-authenticated. Negative-path specs opt out per-test.
 *
 * Project ordering is expressed via `dependencies`, so Playwright runs
 * bootstrap → auth → first-run → e2e in order regardless of file discovery.
 * `first-run` exists because the suite shares ONE real DB: the dashboard's
 * first-run swap can only be observed before the agent-registering specs in
 * the main `e2e` project mutate the workspace.
 */
export default defineConfig({
	testDir: './e2e/docker',
	timeout: 30_000,
	// CI retries flaky reads; the setup projects override this to 0 below because
	// creating the first admin is a non-idempotent mutation that must not re-run.
	retries: process.env.CI ? 2 : 0,
	// REQUIRED: the bootstrap project creates the first admin (a non-idempotent
	// mutation) against a single shared DB. Serial execution is what guarantees it
	// can't interleave with itself or with auth setup — do not bump this for speed.
	workers: 1,
	reporter: [['html', { open: 'never' }]],
	use: {
		baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
		// `on-first-retry` traces capture localStorage, which on the authenticated
		// `e2e` project includes the admin JWT. This is an ACCEPTED disclosure: the
		// token is short-lived (expires_in 3600) and minted against an ephemeral CI
		// Postgres that is destroyed in the `always()` teardown, so any token in an
		// uploaded report is already dead on arrival. Traces are kept because they
		// are the primary signal for debugging real-backend flakes.
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
	},
	projects: [
		{
			name: 'bootstrap',
			testMatch: /bootstrap\.setup\.ts/,
			// Non-idempotent DB mutation — never retry it.
			retries: 0,
		},
		{
			name: 'auth',
			testMatch: /auth\.setup\.ts/,
			dependencies: ['bootstrap'],
			retries: 0,
		},
		{
			// Specs that assert the pristine, never-touched workspace (the
			// dashboard's first-run checklist). Must run before `e2e` specs
			// register agents / run executions against the shared DB.
			name: 'first-run',
			testMatch: /first-run\.spec\.ts/,
			dependencies: ['auth'],
			use: { storageState: STORAGE_STATE_PATH },
		},
		{
			name: 'e2e',
			testMatch: /.*\.spec\.ts/,
			testIgnore: /first-run\.spec\.ts/,
			dependencies: ['first-run'],
			use: { storageState: STORAGE_STATE_PATH },
		},
	],
});
