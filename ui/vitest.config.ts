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
			'react-router-dom',
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
					context: { reducedMotion: 'reduce' },
				},
			],
		},
		globals: true,
		setupFiles: ['./src/__tests__/setup.ts'],
		include: ['src/**/*.test.{ts,tsx}'],
		// `*.lint.test.ts` are Node-only (they drive ESLint's programmatic API);
		// they run under vitest.lint.config.ts, not the browser provider.
		exclude: [...configDefaults.exclude, 'src/**/*.lint.test.ts'],
		// Browser-mode tests share a single Chromium page; on a loaded CI runner
		// async React re-renders and MSW responses can land mid-assertion and
		// trip a transient duplicate/not-yet-narrowed match. Retry on CI only —
		// real regressions fail every attempt, so this won't mask them. Mirrors
		// the Playwright config's `retries: process.env.CI ? 2 : 0`.
		retry: process.env.CI ? 2 : 0,
		coverage: {
			provider: 'istanbul',
			reporter: ['text', 'html', 'lcov'],
			include: ['src/shared/**', 'src/modules/**'],
			exclude: ['src/shared/api/generated/**'],
		},
	},
});
