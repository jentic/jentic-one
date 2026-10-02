/**
 * A realistic day-and-a-half of platform events for Monitor's Everything feed
 * in mocked (Mode A) dev + e2e.
 *
 * `/events` is answered by the shell rail's stateful store (it registers
 * before Monitor in src/mocks/handlers.ts), so rather than reorder the
 * handler table — which would swap the rail's and the Home page's data out
 * from under them — this APPENDS to that one store. Everything seeded here is
 * older than the rail's own three seeds and nothing waits on a human (no
 * unacknowledged `requires_action`), so the rail's newest rows, the bell
 * count and the Home inbox read exactly as before.
 *
 * Not seeded under Vitest: unit tests reset the store after each test and
 * expect only the rail's fixtures.
 */
import { seedRailEvents } from '@/shared/app/rail/mocks/handlers';

const MIN = 60;
const HOUR = 60 * MIN;
const ago = (sec: number) => new Date(Date.now() - sec * 1000).toISOString();

type Seed = Parameters<typeof seedRailEvents>[0][number];

function call(i: number, sec: number, op: string, actor: string, failed = false): Seed {
	const id = `mix_exec_${i}`;
	return {
		event_id: `evt_${id}`,
		type: failed ? 'execution.failed' : 'execution.completed',
		severity: failed ? 'error' : 'info',
		summary: `Execution ${failed ? 'failed' : 'completed'}: ${op}`,
		detail: failed ? 'upstream returned 502 Bad Gateway' : null,
		created_at: ago(sec),
		// A failure the operator already looked at — history, not an alert.
		requires_action: failed,
		acknowledged: failed,
		acknowledged_at: failed ? ago(sec - 5 * MIN) : null,
		acknowledged_by: failed ? 'admin@local' : null,
		trace_id: `tr_${id}`,
		actor_id: actor,
		actor_type: 'agent',
		data: { trace_id: `tr_${id}`, execution_id: id },
		_links: { self: `/events/evt_${id}`, execution: `/executions/${id}` },
	};
}

const MIX: Seed[] = [
	// This morning — a burst of routine calls (folds into one row).
	call(1, 3 * MIN, 'github.issues.list', 'support-triage'),
	call(2, 4 * MIN, 'github.issues.get', 'support-triage'),
	call(3, 6 * MIN, 'github.issues.comment', 'support-triage'),
	call(4, 7 * MIN, 'zendesk.tickets.update', 'support-triage'),
	call(5, 9 * MIN, 'zendesk.tickets.list', 'support-triage'),
	{
		event_id: 'evt_mix_cred_expiring',
		type: 'credential.expiring_soon',
		severity: 'warning',
		summary: 'Credential expiring soon: Stripe (live)',
		detail: 'expires in 6 days',
		created_at: ago(22 * MIN),
		data: { credential_id: 'cred_stripe_live' },
	},
	call(6, 41 * MIN, 'stripe.invoices.create', 'invoice-bot'),
	call(7, 44 * MIN, 'stripe.invoices.send', 'invoice-bot'),
	{
		event_id: 'evt_mix_agent_approved',
		type: 'agent.registration_approved',
		severity: 'info',
		summary: 'Agent approved: support-agent',
		created_at: ago(1 * HOUR + 12 * MIN),
		actor_id: 'usr_admin_1',
		actor_type: 'user',
		data: { agent_id: 'agnt_active_1' },
	},
	call(8, 1 * HOUR + 40 * MIN, 'slack.chat.postMessage', 'agnt_active_1', true),
	{
		event_id: 'evt_mix_catalog_update',
		type: 'catalog.update_available',
		severity: 'warning',
		summary: 'Update available: GitHub REST API 1.1.4 → 1.2.0',
		created_at: ago(2 * HOUR + 5 * MIN),
		data: { api_id: 'github', vendor: 'github.com', name: 'rest', version: '1.2.0' },
	},
	call(9, 2 * HOUR + 30 * MIN, 'hubspot.contacts.search', 'agnt_active_1'),
	call(10, 2 * HOUR + 31 * MIN, 'hubspot.contacts.update', 'agnt_active_1'),
	call(11, 2 * HOUR + 33 * MIN, 'hubspot.deals.list', 'agnt_active_1'),
	// Yesterday and before.
	{
		event_id: 'evt_mix_import_done',
		type: 'import.completed',
		severity: 'info',
		summary: 'Import completed: stripe-api',
		detail: '312 operations',
		created_at: ago(26 * HOUR),
		actor_id: 'usr_admin_1',
		actor_type: 'user',
		data: { job_id: 'job_import_2' },
		_links: { self: '/events/evt_mix_import_done', job: '/jobs/job_import_2' },
	},
	call(12, 27 * HOUR, 'stripe.customers.list', 'invoice-bot'),
	call(13, 27 * HOUR + 3 * MIN, 'stripe.customers.retrieve', 'invoice-bot', true),
	{
		event_id: 'evt_mix_import_failed',
		type: 'import.failed',
		severity: 'error',
		summary: 'Import failed: legacy-crm',
		detail: 'spec is not valid OpenAPI 3.x',
		created_at: ago(29 * HOUR),
		requires_action: true,
		acknowledged: true,
		acknowledged_at: ago(28 * HOUR),
		acknowledged_by: 'admin@local',
		actor_id: 'usr_admin_1',
		actor_type: 'user',
		data: { job_id: 'job_exec_3' },
		_links: { self: '/events/evt_mix_import_failed', job: '/jobs/job_exec_3' },
	},
	{
		event_id: 'evt_mix_agent_registered',
		type: 'agent.self_registered',
		severity: 'warning',
		summary: 'Agent registered: nightly-reporter',
		created_at: ago(31 * HOUR),
		requires_action: true,
		acknowledged: true,
		acknowledged_at: ago(30 * HOUR),
		acknowledged_by: 'admin@local',
		actor_id: 'nightly-reporter',
		actor_type: 'agent',
	},
	call(14, 33 * HOUR, 'github.repos.list', 'support-triage'),
	call(15, 33 * HOUR + 2 * MIN, 'github.pulls.list', 'support-triage'),
	call(16, 33 * HOUR + 4 * MIN, 'github.pulls.get', 'support-triage'),
	call(17, 33 * HOUR + 5 * MIN, 'github.pulls.review', 'support-triage'),
];

const DEV = import.meta.env.MODE !== 'test';
if (DEV) seedRailEvents(MIX);

// ── The records behind the feed ───────────────────────────────────────────
// Opening an Everything row resolves its trace / job, so the calls above get
// matching execution rows (and a few audited actions), again dev-only: the
// Monitor unit tests page over their own three-row fixture.

const VENDOR: Record<string, { host: string; credential: string }> = {
	github: { host: 'api.github.com', credential: 'GitHub App' },
	zendesk: { host: 'acme.zendesk.com', credential: 'Zendesk token' },
	stripe: { host: 'api.stripe.com', credential: 'Stripe (live)' },
	slack: { host: 'slack.com', credential: 'Slack bot' },
	hubspot: { host: 'api.hubapi.com', credential: 'HubSpot private app' },
};

function executionFor(seed: Seed) {
	const id = (seed.data as { execution_id: string }).execution_id;
	const op = seed.summary.replace(/^Execution \w+: /, '');
	const vendor = op.split('.')[0];
	const failed = seed.type === 'execution.failed';
	const n = Number(id.replace(/\D/g, ''));
	const at = seed.created_at ?? new Date().toISOString();
	return {
		_links: { self: `/executions/${id}` },
		actor_id: seed.actor_id,
		actor_type: 'agent',
		api: { vendor, name: `${vendor}-api`, version: 'v1', host: VENDOR[vendor]?.host ?? null },
		created_at: at,
		duration_ms: failed ? 30_000 : 180 + ((n * 137) % 900),
		error: failed ? 'Upstream returned 502 Bad Gateway' : null,
		execution_id: id,
		http_status: failed ? 502 : 200,
		operation_id: op,
		origin: n % 3 === 0 ? 'mcp' : 'api',
		pinned_revisions: null,
		started_at: at,
		status: failed ? 'failed' : 'completed',
		credential_id: `cred_${vendor}`,
		credential_name: VENDOR[vendor]?.credential ?? null,
		trace_id: seed.trace_id,
	};
}

export const DEV_EXECUTIONS = DEV
	? MIX.filter((s) => s.type.startsWith('execution.')).map(executionFor)
	: [];

function audit(
	id: string,
	sec: number,
	action: string,
	target: [type: string, id: string],
	extra: Record<string, unknown> = {},
) {
	return {
		action,
		actor_id: 'usr_admin_1',
		actor_session_id: 'sess_dev_1',
		actor_type: 'user',
		after: null,
		before: null,
		diff: null,
		id,
		ip_address: '192.168.1.24',
		job_id: null,
		occurred_at: ago(sec),
		origin: 'dashboard',
		reason: null,
		request_id: `req_${id}`,
		target_id: target[1],
		target_parent_id: null,
		target_type: target[0],
		trace_id: null,
		user_agent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
		...extra,
	};
}

export const DEV_AUDIT = DEV
	? [
			audit('aud_dev_login', 14 * MIN, 'user.login', ['user', 'usr_admin_1'], {
				target_type: 'session',
				target_id: 'sess_dev_1',
			}),
			audit(
				'aud_dev_approve',
				1 * HOUR + 12 * MIN,
				'agent.approve',
				['agent', 'agnt_active_1'],
				{
					before: { status: 'pending' },
					after: { status: 'active' },
					reason: 'Reviewed scopes with the support team',
				},
			),
			audit('aud_dev_rotate', 3 * HOUR, 'credential.rotate', ['credential', 'cred_stripe'], {
				diff: { expires_at: { before: ago(-6 * 86_400), after: ago(-90 * 86_400) } },
			}),
			audit('aud_dev_toolkit', 5 * HOUR, 'toolkit.update', ['toolkit', 'tk_support'], {
				before: { apis: 3 },
				after: { apis: 4 },
			}),
			audit('aud_dev_import', 26 * HOUR + 4 * MIN, 'job.create', ['job', 'job_import_2'], {
				job_id: 'job_import_2',
				origin: 'cli',
				user_agent: 'jentic-cli/1.4.0',
			}),
			audit(
				'aud_dev_revoke',
				30 * HOUR,
				'oauth_grant.revoke',
				['oauth_grant', 'grant_legacy'],
				{
					reason: 'Client no longer in use',
				},
			),
		]
	: [];
