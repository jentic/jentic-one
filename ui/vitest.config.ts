import { defineConfig } from 'vitest/config';
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
	optimizeDeps: {
		include: [
			'@jentic/arazzo-ui',
			'react-dom/client',
			'react-markdown',
			'remark-gfm',
			'rehype-raw',
			'rehype-sanitize',
		],
	},
	test: {
		browser: {
			enabled: true,
			provider: playwright(),
			instances: [
				{
					browser: 'chromium',
					// Tell Chromium to honour reduced-motion so framer-motion's
					// entrance animations resolve to their final state immediately.
					// Without this, axe colour-contrast checks see mid-animation
					// opacity values and report false positives.
					context: { reducedMotion: 'reduce' },
				},
			],
		},
		globals: true,
		setupFiles: ['./src/__tests__/setup.ts'],
		include: ['src/**/*.test.{ts,tsx}'],
		coverage: {
			provider: 'istanbul',
			reporter: ['text', 'html', 'lcov'],
			include: ['src/components/ui/**', 'src/pages/**', 'src/hooks/**'],
			exclude: ['src/api/generated/**'],
		},
	},
});
