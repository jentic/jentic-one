/**
 * Monitor for a caller who can't see what they asked for: the Activity feed
 * shows a plain "No access" state (never a raw error, never a reconnect loop)
 * when events are off-limits, and a call record the caller can't see reads as
 * not found, whether it was opened by execution id or by trace.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, within, checkA11y } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import { Toaster } from '@/shared/ui';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

function viewer(permissions: string[]) {
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

function renderMonitor(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<MonitorPage />
			<Toaster />
		</AuthProvider>,
		{ route },
	);
}

/** Count event reads; `list` / `stream` answer with the given statuses (200 = mocks). */
function trackEventReads({ list = 200, stream = 200 }: { list?: number; stream?: number } = {}) {
	const calls = { list: 0, stream: 0 };
	worker.use(
		http.get('/events', () => {
			calls.list += 1;
			return list === 200
				? HttpResponse.json({ data: [], has_more: false, next_cursor: null })
				: new HttpResponse(null, { status: list });
		}),
		http.get('/events/stream', () => {
			calls.stream += 1;
			return new HttpResponse(null, { status: stream });
		}),
	);
	return calls;
}

describe('Monitor Activity feed — caller without event access', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it('shows a clear no-access state and reads no events without events:read', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer([]))));
		const calls = trackEventReads();
		const { container } = renderMonitor('/app/monitor?view=activity');

		expect(await screen.findByText('No access to activity')).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
		await new Promise((r) => setTimeout(r, 500));
		expect(calls).toEqual({ list: 0, stream: 0 });
		await checkA11y(container);
	});

	it('treats a 403 on the stream as terminal: one attempt, then the no-access state', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(['events:read']))));
		const calls = trackEventReads({ stream: 403 });
		renderMonitor('/app/monitor?view=activity');

		expect(await screen.findByText('No access to activity')).toBeInTheDocument();
		// The feed's reconnect backoff starts at 2s; wait past it.
		await new Promise((r) => setTimeout(r, 2600));
		expect(calls.stream).toBe(1);
		expect(screen.queryByText('Reconnecting…')).not.toBeInTheDocument();
	});

	it('shows the no-access state, not a raw error, when the history read is refused', async () => {
		worker.use(http.get('/users/me', () => HttpResponse.json(viewer(['events:read']))));
		trackEventReads({ list: 403 });
		renderMonitor('/app/monitor?view=activity');

		expect(await screen.findByText('No access to activity')).toBeInTheDocument();
		expect(screen.queryByRole('alert')).not.toBeInTheDocument();
	});
});

describe('Monitor call record — nothing visible', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it('an execution deep link the server answers 404 for reads as not found', async () => {
		renderMonitor('/app/monitor?show=calls&execution_id=exec_not_visible');
		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Call not found')).toBeInTheDocument();
		expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
	});

	it('a trace with no visible executions shows the same not-found state', async () => {
		renderMonitor('/app/monitor?show=calls&trace_id=trace_not_visible');
		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Call not found')).toBeInTheDocument();
		expect(within(dialog).queryByText(/No actor recorded/)).not.toBeInTheDocument();
		expect(within(dialog).queryByText('Calls (0)')).not.toBeInTheDocument();
	});
});
