/**
 * An execution deep link the server answers 404 for (another user's call, or
 * none) reads "Call not found" after ONE read — a 404 is the answer, so the
 * query does not retry it. Rendered under the app's own QueryClient
 * (`appQueryClient`: `createQueryClient` with no backoff), so the assertions
 * cover the retry behaviour the app ships: `useExecution`'s 404 guard composed
 * with the client's default 4xx policy.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { AuthProvider } from '@/shared/auth';
import MonitorPage from '@/modules/monitor/pages/MonitorPage';
import { monitorHandlers } from '@/modules/monitor/mocks/handlers';
import { appQueryClient } from '@/modules/monitor/__tests__/appQueryClient';

function renderMonitor(route: string, client: QueryClient = appQueryClient()) {
	return render(
		<QueryClientProvider client={client}>
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
			const detail = status === 404 ? 'Execution not found' : 'Service unavailable';
			return HttpResponse.json({ detail }, { status });
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
		expect(calls.reads).toBe(1);
	});

	it("doesn't retry a 404 even when the client's default policy would", async () => {
		// A plain retry count has no 4xx rule, so only the hook's own 404 guard
		// stops the retry here.
		const client = new QueryClient({
			defaultOptions: { queries: { retry: 2, retryDelay: 0 } },
		});
		const calls = trackExecutionReads('exec_foreign', 404);
		renderMonitor('/app/monitor?show=calls&execution_id=exec_foreign', client);

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByText('Call not found')).toBeInTheDocument();
		expect(calls.reads).toBe(1);
	});

	it('still retries a server error', async () => {
		const calls = trackExecutionReads('exec_flaky', 503);
		renderMonitor('/app/monitor?show=calls&execution_id=exec_flaky');

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByRole('alert')).toBeInTheDocument();
		// The first read plus the default policy's two retries.
		expect(calls.reads).toBe(3);
	});
});
