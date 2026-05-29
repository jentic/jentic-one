import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { worker } from './mocks/browser';
// Import client to ensure OpenAPI.BASE is set to '' before any tests run
import '@/api/client';
// Load the app stylesheet so Tailwind utilities resolve to real colours and
// `body { @apply bg-background }` is applied. Without it, axe colour-contrast
// checks see white-on-white because the dark-theme background is missing.
import '@/index.css';

beforeAll(async () => {
	await worker.start({ onUnhandledRequest: 'warn' });
});

afterEach(() => {
	cleanup();
	worker.resetHandlers();
	window.localStorage.clear();
	window.sessionStorage.clear();
});

afterAll(() => {
	worker.stop();
});
