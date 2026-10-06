/**
 * Approvals MSW handlers + in-memory store.
 *
 * Mirrors the backend execution-approvals surface and its response codes:
 *
 *   GET  /executions/approvals             → 200 page (`?state=` filters)
 *   GET  /executions/approvals/{id}        → 200 detail | 404 (not a reviewer / missing)
 *   POST /executions/approvals/{id}:decide → 200 updated row | 409 not pending | 422 bad decision
 *
 * Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

interface ApprovalRow {
	id: string;
	job_id: string;
	agent_id: string;
	agent_name: string | null;
	agent_owner_id: string | null;
	credential_id: string;
	api_vendor: string;
	api_name: string;
	api_version: string;
	operation_id: string | null;
	method: string;
	path: string;
	matched_rule_id: string | null;
	state: string;
	expires_at: string;
	decided_at: string | null;
	decided_by: string | null;
	decision_reason: string | null;
	trace_id: string | null;
	execution_id: string | null;
	created_at: string;
	updated_at: string | null;
	request: { method: string; url: string; body: string | null; body_truncated: boolean } | null;
}

const hours = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString();

function seed(): ApprovalRow[] {
	return [
		{
			id: 'exap_pending1',
			job_id: 'job_pending1',
			agent_id: 'agnt_scout',
			agent_name: 'scout',
			agent_owner_id: 'usr_owner',
			credential_id: 'cred_stripe',
			api_vendor: 'api.stripe.com',
			api_name: 'payments',
			api_version: '2024-01-01',
			operation_id: 'createCharge',
			method: 'POST',
			path: '/v1/charges',
			matched_rule_id: 'apr_hold_posts',
			state: 'pending',
			expires_at: hours(23),
			decided_at: null,
			decided_by: null,
			decision_reason: null,
			trace_id: null,
			execution_id: null,
			created_at: hours(-1),
			updated_at: null,
			request: {
				method: 'POST',
				url: 'https://api.stripe.com/v1/charges?expand=balance',
				body: '{"amount":500,"currency":"eur"}',
				body_truncated: false,
			},
		},
		{
			id: 'exap_denied1',
			job_id: 'job_denied1',
			agent_id: 'agnt_scout',
			agent_name: 'scout',
			agent_owner_id: 'usr_owner',
			credential_id: 'cred_stripe',
			api_vendor: 'api.stripe.com',
			api_name: 'payments',
			api_version: '2024-01-01',
			operation_id: 'createRefund',
			method: 'POST',
			path: '/v1/refunds',
			matched_rule_id: 'apr_hold_posts',
			state: 'denied',
			expires_at: hours(20),
			decided_at: hours(-2),
			decided_by: 'usr_owner',
			decision_reason: 'Not this customer',
			trace_id: null,
			execution_id: null,
			created_at: hours(-3),
			updated_at: hours(-2),
			request: null,
		},
	];
}

let store: ApprovalRow[] = seed();

/** Reset the in-memory store between tests. */
export function resetApprovalsStore(): void {
	store = seed();
}

function links(row: ApprovalRow) {
	return { self: `/executions/approvals/${row.id}`, job: `/jobs/${row.job_id}` };
}

function listView(row: ApprovalRow) {
	const { agent_name: _n, agent_owner_id: _o, request: _r, ...rest } = row;
	return { ...rest, _links: links(row) };
}

export const approvalsHandlers = [
	http.get('/executions/approvals', ({ request }) => {
		const state = new URL(request.url).searchParams.get('state');
		const data = store.filter((r) => !state || r.state === state).map(listView);
		return HttpResponse.json({ data, has_more: false, next_cursor: null });
	}),
	http.get('/executions/approvals/:id', ({ params }) => {
		const row = store.find((r) => r.id === params.id);
		if (!row) {
			return HttpResponse.json(
				{ type: 'execution_approval_not_found', status: 404, detail: 'not found' },
				{ status: 404 },
			);
		}
		return HttpResponse.json({ ...row, _links: links(row) });
	}),
	http.post('/executions/approvals/:id\\:decide', async ({ params, request }) => {
		const row = store.find((r) => r.id === params.id);
		if (!row) return HttpResponse.json({ status: 404 }, { status: 404 });
		const body = (await request.json()) as { decision?: string; reason?: string | null };
		if (body.decision !== 'approve' && body.decision !== 'deny') {
			return HttpResponse.json({ status: 422, detail: 'invalid decision' }, { status: 422 });
		}
		if (row.state !== 'pending') {
			return HttpResponse.json(
				{
					type: 'execution_approval_already_decided',
					status: 409,
					detail: `Execution approval '${row.id}' is already ${row.state}`,
				},
				{ status: 409 },
			);
		}
		row.state = body.decision === 'approve' ? 'approved' : 'denied';
		row.decided_at = new Date().toISOString();
		row.decided_by = 'usr_owner';
		row.decision_reason = body.reason ?? null;
		return HttpResponse.json(listView(row));
	}),
];
