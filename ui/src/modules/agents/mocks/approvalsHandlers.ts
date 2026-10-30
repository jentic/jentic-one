/**
 * Approvals MSW handlers + in-memory store.
 *
 * Mirrors the backend held-executions surface and its response codes:
 *
 *   GET  /executions/holds              → 200 page (`?state=` / `?agent_id=` filter)
 *   GET  /executions/holds/{id}         → 200 detail | 404 `held_execution_not_found`
 *   POST /executions/holds/{id}:decide  → 200 updated row | 409 `held_execution_already_settled`
 *                                         | 422 bad decision
 *   POST /executions/holds/{id}:withdraw → 200 withdrawn row | 409 `held_execution_already_settled`
 *   GET  /users                         → 200 page of the org's users (the review
 *                                         pop-up counts the org admins who can decide)
 *
 * The store keeps each row's `job_id` to stand in for the backend's join; it
 * never reaches the wire. Approving moves `execution_status` to `queued`;
 * denying to `failed`; withdrawing to `cancelled`. A row's `agent_reason` is
 * the agent's own reason for the call, which the backend sends once the broker
 * stores one; it is null on most rows, as it is for every agent today.
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
	execution_status: string | null;
	expires_at: string;
	decided_at: string | null;
	decided_by: string | null;
	decision_reason: string | null;
	trace_id: string | null;
	execution_id: string | null;
	created_at: string;
	updated_at: string | null;
	request: { method: string; url: string; body: string | null; body_truncated: boolean } | null;
	agent_reason: string | null;
}

const hours = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString();

function seed(): ApprovalRow[] {
	return [
		{
			id: 'held_pending1',
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
			execution_status: 'held',
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
			agent_reason: null,
		},
		{
			id: 'held_denied1',
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
			execution_status: 'failed',
			expires_at: hours(20),
			decided_at: hours(-2),
			decided_by: 'usr_owner',
			decision_reason: 'Not this customer',
			trace_id: null,
			execution_id: null,
			created_at: hours(-3),
			updated_at: hours(-2),
			request: null,
			agent_reason: null,
		},
	];
}

let store: ApprovalRow[] = seed();

/** Reset the in-memory store between tests. */
export function resetApprovalsStore(): void {
	store = seed();
}

/** Empty the store: nothing is held. */
export function clearApprovalsStore(): void {
	store = [];
}

let nextId = 1;

/**
 * Add a pending held call (or any state via `overrides`) and return it. The
 * caller's agent id defaults to the composed agents store's `agnt_active_1`.
 */
export function seedMockApproval(overrides: Partial<ApprovalRow> = {}): ApprovalRow {
	const id = overrides.id ?? `held_seeded_${nextId++}`;
	const row: ApprovalRow = {
		id,
		job_id: `job_${id}`,
		agent_id: 'agnt_active_1',
		agent_name: null,
		agent_owner_id: 'usr_viewer_1',
		credential_id: 'cred_stripe',
		api_vendor: 'api.stripe.com',
		api_name: 'payments',
		api_version: '2024-01-01',
		operation_id: 'createCharge',
		method: 'POST',
		path: '/v1/charges',
		matched_rule_id: 'apr_hold_posts',
		state: 'pending',
		execution_status: 'held',
		expires_at: hours(23),
		decided_at: null,
		decided_by: null,
		decision_reason: null,
		trace_id: null,
		execution_id: null,
		created_at: hours(-0.5),
		updated_at: null,
		request: null,
		agent_reason: null,
		...overrides,
	};
	store.push(row);
	return row;
}

/** The store's current row for `id` — tests assert a decision landed. */
export function getMockApproval(id: string): ApprovalRow | undefined {
	return store.find((r) => r.id === id);
}

/** Execution statuses with a result to collect. */
const FINISHED = new Set(['completed', 'failed']);

function links(row: ApprovalRow) {
	return {
		self: `/executions/holds/${row.id}`,
		result: FINISHED.has(row.execution_status ?? '')
			? `/executions/holds/${row.id}/result`
			: null,
		withdraw: row.state === 'pending' ? `/executions/holds/${row.id}:withdraw` : null,
		// The hold's `execution_id` is set once the approved call runs.
		execution: row.execution_id ? `/executions/${row.execution_id}` : null,
	};
}

function detailView(row: ApprovalRow) {
	const { job_id: _j, ...rest } = row;
	return {
		...rest,
		review_url: `${window.location.origin}/app/agents/approvals/${row.id}`,
		_links: links(row),
	};
}

function listView(row: ApprovalRow) {
	const { agent_name: _n, agent_owner_id: _o, request: _r, ...rest } = detailView(row);
	return rest;
}

/**
 * The org's users as an org admin lists them (`GET /users`): two admins who
 * can decide any held call (the mock signed-in admin and one more), a member
 * who can't, and an admin whose invite nobody redeemed yet.
 */
const ORG_USERS = [
	{
		id: '00000000-0000-0000-0000-000000000001',
		name: 'Admin User',
		admin: true,
		invite: 'accepted',
	},
	{ id: 'usr_priya_shah', name: 'Priya Shah', admin: true, invite: 'accepted' },
	{ id: 'usr_tomas_ortega', name: 'Tomás Ortega', admin: false, invite: 'accepted' },
	{ id: 'usr_dana_lee', name: 'Dana Lee', admin: true, invite: 'pending' },
].map((u) => {
	const [first_name, ...rest] = u.name.split(' ');
	return {
		id: u.id,
		name: u.name,
		first_name,
		last_name: rest.join(' '),
		email: `${first_name.toLowerCase()}@example.test`,
		active: true,
		auth_provider: 'local',
		external_subject_id: null,
		invite_state: u.invite,
		must_change_password: false,
		permissions: {
			assigned: u.admin ? ['org:admin'] : ['agents:read'],
			effective: (u.admin ? ['org:admin'] : ['agents:read']).map((name) => ({
				name,
				implied_by: null,
			})),
		},
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
	};
});

function alreadySettled(row: ApprovalRow) {
	return HttpResponse.json(
		{
			type: 'held_execution_already_settled',
			status: 409,
			detail: `Held execution '${row.id}' is already ${row.state}`,
		},
		{ status: 409 },
	);
}

export const approvalsHandlers = [
	http.get('/executions/holds', ({ request }) => {
		const params = new URL(request.url).searchParams;
		const state = params.get('state');
		const agentId = params.get('agent_id');
		const data = store
			.filter((r) => (!state || r.state === state) && (!agentId || r.agent_id === agentId))
			.map(listView);
		return HttpResponse.json({ data, has_more: false, next_cursor: null });
	}),
	http.get('/executions/holds/:id', ({ params }) => {
		const row = store.find((r) => r.id === params.id);
		if (!row) {
			return HttpResponse.json(
				{ type: 'held_execution_not_found', status: 404, detail: 'not found' },
				{ status: 404 },
			);
		}
		return HttpResponse.json(detailView(row));
	}),
	http.post('/executions/holds/:id\\:decide', async ({ params, request }) => {
		const row = store.find((r) => r.id === params.id);
		if (!row) return HttpResponse.json({ status: 404 }, { status: 404 });
		const body = (await request.json()) as { decision?: string; reason?: string | null };
		if (body.decision !== 'approve' && body.decision !== 'deny') {
			return HttpResponse.json({ status: 422, detail: 'invalid decision' }, { status: 422 });
		}
		if (row.state !== 'pending') {
			return alreadySettled(row);
		}
		row.state = body.decision === 'approve' ? 'approved' : 'denied';
		row.execution_status = body.decision === 'approve' ? 'queued' : 'failed';
		row.decided_at = new Date().toISOString();
		// The mock signed-in admin (`/users/me`), so the receipt reads "by you".
		row.decided_by = '00000000-0000-0000-0000-000000000001';
		row.decision_reason = body.reason ?? null;
		return HttpResponse.json(listView(row));
	}),
	http.post('/executions/holds/:id\\:withdraw', ({ params }) => {
		const row = store.find((r) => r.id === params.id);
		if (!row) return HttpResponse.json({ status: 404 }, { status: 404 });
		if (row.state !== 'pending') {
			return alreadySettled(row);
		}
		row.state = 'withdrawn';
		row.execution_status = 'cancelled';
		row.updated_at = new Date().toISOString();
		return HttpResponse.json(listView(row));
	}),
	http.get('/users', () =>
		HttpResponse.json({ data: ORG_USERS, has_more: false, next_cursor: null }),
	),
];
