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

// Every file renders on the product's default palette, so axe colour-contrast
// checks see one theme. A stored choice left by another file is cleared first
// so `shared/lib/theme` reads the default when it loads.
window.localStorage.removeItem('jentic-one.theme');
document.documentElement.dataset.theme = 'light';

// All test files run in parallel inside a single Chromium, so on a loaded CI
// runner an async settle (backlog fetch → React commit) can outlast
// testing-library's 1s default and fail every retry of the same loaded run
// (e.g. the AgentRail feed assertions). Passing queries are unaffected — they
// resolve as soon as the element appears; only genuine failures wait longer.
//
// `reactStrictMode` mounts every `render`/`renderHook` tree under a root-level
// <StrictMode>, so effects run mount → unmount → remount as they do in dev. In
// React 19 strict effect semantics only engage when StrictMode is the root
// element passed to `createRoot().render()`; a <StrictMode> inside a test
// wrapper sits below RTL's root and is a silent no-op. A test that has to
// observe single-mount behaviour can pass `reactStrictMode: false` to `render`.
configure({ asyncUtilTimeout: 5_000, reactStrictMode: true });

beforeAll(async () => {
	await worker.start({ onUnhandledFrame: 'warn' });
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
	document.documentElement.dataset.theme = 'light';
});

afterAll(() => {
	// Fire-and-forget: awaiting would block on in-flight requests that some
	// suites intentionally leave pending (e.g. never-resolving handlers).
	void worker.stop();
});
