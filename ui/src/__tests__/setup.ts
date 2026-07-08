import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { worker } from '@oss-internal/mocks/browser';
import { resetAgentsStore } from '@oss-internal/modules/agents/mocks/handlers';
import { resetRailEventsStore } from '@oss-internal/shared/app/rail/mocks/handlers';
// Load the app stylesheet so Tailwind utilities resolve to real colours (e.g.
// `body { @apply bg-background }`). Without it, axe colour-contrast checks see
// white-on-white because the theme background is missing.
import '@oss-internal/index.css';

beforeAll(async () => {
	await worker.start({ onUnhandledRequest: 'warn' });
});

afterEach(() => {
	cleanup();
	worker.resetHandlers();
	// Reset the mutable MSW store so a lifecycle mutation in one test can't leak
	// into the next (a per-file beforeEach also resets, this is the safety net).
	resetAgentsStore();
	resetRailEventsStore();
	window.localStorage.clear();
	window.sessionStorage.clear();
});

afterAll(() => {
	worker.stop();
});
