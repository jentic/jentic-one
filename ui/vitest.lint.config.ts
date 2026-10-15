import { defineConfig } from 'vitest/config';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Node-environment Vitest project for tests that drive Node-only tooling — in
 * particular the ESLint programmatic API, which can't run inside the default
 * browser-mode project (`vitest.config.ts`). Kept separate so component tests
 * stay in Chromium and lint-rule tests stay in Node.
 *
 * Run via `npm run test:lint`. The convention here: files matching
 * `*.lint.test.ts` are node-only.
 */
export default defineConfig({
	resolve: {
		alias: { '@': resolve(__dirname, 'src') },
	},
	test: {
		environment: 'node',
		globals: true,
		include: ['src/**/*.lint.test.ts'],
		// ESLint must lint real source files from disk; no setup/mocking.
		setupFiles: [],
		// The first `lintText` call in a file pays ESLint's cold start (loading
		// the config, plugins and the TypeScript import resolver) — about a
		// second locally, enough to pass the 5s default on a loaded CI runner.
		testTimeout: 30_000,
	},
});
