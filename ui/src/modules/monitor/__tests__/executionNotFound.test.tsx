/**
 * An execution deep link the server answers 404 for (another user's call, or
 * none) reads "Call not found" after ONE read — a 404 is the answer, so the
 * query does not retry it. Rendered under the app's own QueryClient
 * (`createQueryClient`), whose default policy would otherwise apply.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { createQueryClient } from '@/shared/app';
import { AuthProvider } from '@/shared/auth';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';

function renderMonitor(route: string) {
	return render(
		<QueryClientProvider client={createQueryClient()}>
			<AuthProvider>
				<MemoryRouter initialEntries={[route]}>
					<MonitorPage />
				</MemoryRouter>
			</AuthProvider>
		</QueryClientProvider>,
	);
}

/** Count reads of one execution, answered with `status`. */
function trackExecutionReads(id: string, status: number) {
	const calls = { reads: 0 };
	worker.use(
		http.get('/executions/:id', ({ params }) => {
			if (params.id !== id) return undefined;
			calls.reads += 1;
			return HttpResponse.json({ detail: 'Execution not found' }, { status });
		}),
	);
	return calls;
}

describe('Monitor call record — a foreign execution deep link', () => {
	beforeEach(() => {
		setToken('mock-access-token');
		worker.use(...monitorHandlers);
	});

	it('shows "Call not found" after a single 404, with no retry', async () => {
		const calls = trackExecutionReads('exec_foreign', 404);
		renderMonitor('/app/monitor?show=calls&execution_id=exec_foreign');

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Call not found')).toBeInTheDocument();
		// Past where a first and second retry would have fired.
		await new Promise((r) => setTimeout(r, 3500));
		expect(calls.reads).toBe(1);
	});

	it('still retries a server error', async () => {
		const calls = trackExecutionReads('exec_flaky', 503);
		renderMonitor('/app/monitor?show=calls&execution_id=exec_flaky');

		await screen.findByRole('dialog');
		await new Promise((r) => setTimeout(r, 1800));
		expect(calls.reads).toBeGreaterThan(1);
	});
});
