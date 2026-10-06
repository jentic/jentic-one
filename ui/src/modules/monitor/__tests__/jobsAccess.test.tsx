/**
 * Monitor's Jobs source for a caller who can't read the job queue: a plain
 * "No access to jobs" state, no request without `jobs:read`, and a refused
 * read (403) answered once, never retried, never shown as a raw error.
 *
 * Rendered under the app's own QueryClient (`createQueryClient`), so the
 * retry policy under test is the one the app ships.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { createQueryClient } from '@/shared/app';
import { AuthProvider } from '@/shared/auth';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

/** A member's effective permissions with the usual defaults. */
const MEMBER_DEFAULTS = [
	'agents:read',
	'agents:write',
	'audit:read',
	'credentials:read',
	'events:read',
	'executions:read',
	'jobs:read',
];
const without = (...drop: string[]) => MEMBER_DEFAULTS.filter((p) => !drop.includes(p));

function viewer(permissions: readonly string[]) {
	return {
		id: '00000000-0000-0000-0000-000000000002',
		email: 'member@local',
		first_name: 'Member',
		last_name: 'User',
		active: true,
		permissions,
		must_change_password: false,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
	};
}

function renderJobs() {
	return render(
		<QueryClientProvider client={createQueryClient()}>
			<AuthProvider>
				<MemoryRouter initialEntries={['/app/monitor?show=jobs']}>
					<MonitorPage />
				</MemoryRouter>
			</AuthProvider>
		</QueryClientProvider>,
	);
}

/** Count `/jobs` list reads, answered by the module mocks or with `status`. */
function trackJobReads(status = 200) {
	const calls = { list: 0 };
	worker.events.on('request:start', ({ request }) => {
		if (new URL(request.url).pathname === '/jobs') calls.list += 1;
	});
	if (status !== 200) {
		worker.use(
			http.get('/jobs', () =>
				HttpResponse.json({ detail: 'This action requires one of: jobs:read' }, { status }),
			),
		);
	}
	return calls;
}

describe('Monitor Jobs — read access', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
		return () => worker.events.removeAllListeners();
	});

	it.each([
		['admin', ['org:admin'], true],
		['member with defaults', MEMBER_DEFAULTS, true],
		['member without agents:write', without('agents:write'), true],
		['member without agents:read', without('agents:read', 'agents:write'), true],
		['member without events:read', without('events:read'), true],
		['member without jobs:read', without('jobs:read'), false],
	] as const)('%s', async (_label, permissions, canRead) => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(permissions))));
		const calls = trackJobReads();
		const { container } = renderJobs();

		if (canRead) {
			expect(await screen.findByText('job_import_1')).toBeInTheDocument();
			expect(screen.queryByText('No access to jobs')).toBeNull();
			expect(calls.list).toBeGreaterThan(0);
		} else {
			expect(await screen.findByText('No access to jobs')).toBeInTheDocument();
			expect(screen.queryByRole('alert')).toBeNull();
			await new Promise((r) => setTimeout(r, 500));
			expect(calls.list).toBe(0);
			await checkA11y(container);
		}
	});

	it('shows the no-access state after one refused read, without retrying', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(MEMBER_DEFAULTS))));
		const calls = trackJobReads(403);
		renderJobs();

		expect(await screen.findByText('No access to jobs')).toBeInTheDocument();
		expect(screen.queryByText(/This action requires one of/)).toBeNull();
		expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
		// Past the default retry backoff (1s, then 2s).
		await new Promise((r) => setTimeout(r, 3500));
		expect(calls.list).toBe(1);
	});
});
