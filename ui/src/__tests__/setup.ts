import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { worker } from '@/mocks/browser';
import { clearAllToasts } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { resetRailEventsStore } from '@/shared/app/rail/mocks/handlers';
// Load the app stylesheet so Tailwind utilities resolve to real colours (e.g.
// `body { @apply bg-background }`). Without it, axe colour-contrast checks see
// white-on-white because the theme background is missing.
import '@/index.css';

// All test files run in parallel inside a single Chromium, so on a loaded CI
// runner an async settle (backlog fetch → React commit) can outlast
// testing-library's 1s default and fail every retry of the same loaded run
// (e.g. the AgentRail feed assertions). Passing queries are unaffected — they
// resolve as soon as the element appears; only genuine failures wait longer.
configure({ asyncUtilTimeout: 5_000 });

beforeAll(async () => {
	await worker.start({ onUnhandledRequest: 'warn' });
});

afterEach(() => {
	cleanup();
	worker.resetHandlers();
	// The toast store is module-level and cleanup() only unmounts the Toaster —
	// without this, a success toast from one test re-renders in the next test's
	// fresh <Toaster> and can satisfy its findByText before its own request lands.
	clearAllToasts();
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
