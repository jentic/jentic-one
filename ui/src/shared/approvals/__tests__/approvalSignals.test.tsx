/**
 * The held-call signals (inbox rows, badges, "Waiting for you") read one live
 * query, and settle the moment an approval is decided, withdrawn or expires:
 * each `execution.approval_*` event on the stream refreshes them, and the held
 * job's Monitor slices with them.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { act } from 'react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { render, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import {
	clearToken,
	setToken,
	sharedQueryKeys,
	type ExecutionApprovalResponse,
} from '@/shared/api';
import { AgentStreamProvider } from '@/shared/lib/agentStream';
import {
	PendingApprovalsBadge,
	describeHeldCall,
	groupApprovalsByAgent,
	summariseHeldCalls,
} from '@/shared/approvals';

const page = <T,>(data: T[]) => ({ data, has_more: false, next_cursor: null });

function heldCall(overrides: Partial<ExecutionApprovalResponse> = {}): ExecutionApprovalResponse {
	const id = overrides.id ?? 'exap_1';
	return {
		id,
		job_id: `job_${id}`,
		agent_id: 'agnt_a',
		credential_id: 'cred_1',
		api_vendor: 'api.stripe.com',
		api_name: 'payments',
		api_version: '1',
		method: 'POST',
		path: '/v1/charges',
		state: 'pending' as ExecutionApprovalResponse['state'],
		expires_at: new Date(Date.now() + 3_600_000).toISOString(),
		created_at: new Date(Date.now() - 60_000).toISOString(),
		_links: { self: `/executions/approvals/${id}`, job: `/jobs/job_${id}` },
		...overrides,
	};
}

describe('groupApprovalsByAgent', () => {
	it('groups per agent, oldest call first, the longest-waiting agent leading', () => {
		const groups = groupApprovalsByAgent([
			heldCall({ id: 'b2', agent_id: 'b', created_at: '2026-01-01T00:03:00Z' }),
			heldCall({ id: 'a1', agent_id: 'a', created_at: '2026-01-01T00:02:00Z' }),
			heldCall({ id: 'b1', agent_id: 'b', created_at: '2026-01-01T00:01:00Z' }),
		]);
		expect(groups.map((g) => g.agentId)).toEqual(['b', 'a']);
		expect(groups[0].approvals.map((a) => a.id)).toEqual(['b1', 'b2']);
		expect(groups[0].since).toBe('2026-01-01T00:01:00Z');
	});

	it('describes one call, and counts several', () => {
		const one = heldCall();
		expect(describeHeldCall(one)).toBe('POST /v1/charges on api.stripe.com/payments');
		expect(summariseHeldCalls([one])).toBe('a call to api.stripe.com/payments');
		expect(summariseHeldCalls([one, heldCall({ id: 'exap_2' })])).toBe('2 calls');
	});
});

describe('held-call signals settle on approval events', () => {
	let rows: ExecutionApprovalResponse[];
	let jobReads: number;
	let push: ((frame: string) => void) | null;

	beforeEach(() => {
		setToken('test-token');
		rows = [heldCall()];
		jobReads = 0;
		push = null;
		worker.use(
			http.get('/executions/approvals', () => HttpResponse.json(page(rows))),
			http.get('/events', () => HttpResponse.json(page([]))),
			http.get(
				'/events/stream',
				() =>
					new HttpResponse(
						new ReadableStream({
							start(controller) {
								const enc = new TextEncoder();
								push = (frame) => controller.enqueue(enc.encode(frame));
							},
						}),
						{ headers: { 'Content-Type': 'text/event-stream' } },
					),
			),
		);
	});

	afterEach(() => clearToken());

	/** Stands in for Monitor's held-job slice under the shared root. */
	function JobProbe() {
		useQuery({
			queryKey: [...sharedQueryKeys.monitorJobRoot, 'job_exap_1'],
			queryFn: async () => {
				jobReads += 1;
				return { job_id: 'job_exap_1' };
			},
			staleTime: Infinity,
		});
		return null;
	}

	function renderSignals() {
		const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
		return render(
			<QueryClientProvider client={client}>
				<MemoryRouter>
					<AgentStreamProvider live={true}>
						<PendingApprovalsBadge />
						<JobProbe />
					</AgentStreamProvider>
				</MemoryRouter>
			</QueryClientProvider>,
		);
	}

	it.each([
		'execution.approval_decided',
		'execution.approval_withdrawn',
		'execution.approval_expired',
	])('%s clears the badge and refreshes the held job', async (type) => {
		renderSignals();
		expect(await screen.findByLabelText('1 call awaiting your approval')).toBeInTheDocument();
		await waitFor(() => expect(push).not.toBeNull());
		await waitFor(() => expect(jobReads).toBe(1));

		rows = [];
		const event = {
			_links: { self: '/events/evt_settled' },
			event_id: `evt_${type}`,
			type,
			severity: 'info',
			summary: 'Execution approval exap_1 settled',
			created_at: new Date().toISOString(),
			requires_action: false,
			job_id: 'job_exap_1',
			data: { approval_id: 'exap_1', agent_id: 'agnt_a' },
		};
		act(() =>
			push?.(`event: ${type}\nid: ${event.event_id}\ndata: ${JSON.stringify(event)}\n\n`),
		);

		await waitFor(() =>
			expect(screen.queryByLabelText(/awaiting your approval/)).not.toBeInTheDocument(),
		);
		await waitFor(() => expect(jobReads).toBe(2));
	});
});
