import { defineConfig } from 'vitest/config';
import { configDefaults } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { playwright } from '@vitest/browser-playwright';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
	plugins: [react(), tailwindcss()],
	resolve: {
		alias: {
			'@': resolve(__dirname, 'src'),
		},
	},
	// Pre-bundle test-only deps so Vitest's browser provider doesn't discover
	// and re-optimize them mid-run (which triggers a reload and flaky/duplicate
	// test execution). Keep in sync with deps imported from test-utils/setup.
	optimizeDeps: {
		include: [
			'@tanstack/react-query',
			'react-router',
			'@testing-library/react',
			'@testing-library/user-event',
			'axe-core',
		],
	},
	test: {
		browser: {
			enabled: true,
			provider: playwright(),
			headless: true,
			instances: [
				{
					browser: 'chromium',
					// Honour reduced-motion so any future entrance animations
					// resolve to their final state immediately. Without this, axe
					// colour-contrast checks can fire mid-animation and report
					// false positives on translucent elements.
					//
					// Pin timezone + locale so `toLocaleDateString`/`toLocaleTimeString`
					// output (rail day/time labels, #705) is deterministic across
					// developer machines and CI — otherwise a region-dependent month
					// abbreviation makes assertions like `/Jul/` only-green-on-CI.
					// UTC is DST-free; the DST-specific "Yesterday" test
					// temporarily overrides the in-page timezone to America/New_York
					// via CDP (`Emulation.setTimezoneOverride`) for that one test and
					// restores UTC in a `finally`, so it exercises a real spring-forward
					// boundary without weakening this global pin for the rest of the
					// suite.
					context: {
						reducedMotion: 'reduce',
						timezoneId: 'UTC',
						locale: 'en-US',
					},
				},
			],
		},
		globals: true,
		// Pin locale/timezone env for any Node-side date logic too; the browser
		// context above pins the in-page `Date`/`Intl` behaviour. (POSIX locale
		// ids use underscores — `en_US`, unlike the BCP 47 `en-US` above.)
		env: {
			TZ: 'UTC',
			LANG: 'en_US.UTF-8',
			LC_ALL: 'en_US.UTF-8',
		},
		setupFiles: ['./src/__tests__/setup.ts'],
		include: ['src/**/*.test.{ts,tsx}'],
		// `*.lint.test.ts` are Node-only (they drive ESLint's programmatic API);
		// they run under vitest.lint.config.ts, not the browser provider.
		exclude: [...configDefaults.exclude, 'src/**/*.lint.test.ts'],
		// Browser-mode tests run all files in parallel inside one Chromium, so
		// under load async UI settling (lazy mounts, secondary queries, entrance
		// animations) occasionally outlasts testing-library timeouts — a
		// different marginal test loses the race each full run, locally too.
		// Retry once locally and twice on CI — real regressions fail every
		// attempt, so this won't mask them. Mirrors the Playwright config.
		retry: process.env.CI ? 2 : 1,
		coverage: {
			provider: 'istanbul',
			reporter: ['text', 'html', 'lcov'],
			include: ['src/shared/**', 'src/modules/**'],
			exclude: ['src/shared/api/generated/**'],
		},
	},
});
