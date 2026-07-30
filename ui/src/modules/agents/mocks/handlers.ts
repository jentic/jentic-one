/**
 * Agents MSW handlers + in-memory store.
 *
 * Mirrors the agent / service-account / dynamic-registration surface the module
 * consumes, with a mutable store so lifecycle transitions are observable across
 * calls (approve a pending agent → a later GET shows it active). Shapes match
 * the generated response models; the state machine + response codes mirror what
 * the real backend returns (verified on :8000):
 *
 *   :approve / :deny           → 200 + the updated row
 *   :disable / :enable / DELETE → 204 no body
 *   :deny with empty reason    → 422
 *   invalid transition         → 409
 *
 * Also serves the platform permission catalogue (`GET /permissions`) and the
 * per-actor scope grants (`GET/PUT .../scopes`, #615). The catalogue mirrors the
 * backend's `ALL_PERMISSIONS` verbatim. Like the real actor-scope PUTs, saving
 * does NOT validate scopes against the catalogue or enforce
 * `grantable_by_caller` — only a malformed scope is rejected (422). See
 * {@link validateScopes}.
 *
 * Registered additively in src/mocks/handlers.ts.
 */
import { http, HttpResponse } from 'msw';

type Status = 'pending' | 'active' | 'rejected' | 'disabled' | 'archived';

interface AgentRow {
	id: string;
	name: string;
	description: string | null;
	owner_id: string | null;
	registered_by: string;
	parent_agent_id: string | null;
	approved_by: string | null;
	status: Status;
	denial_reason: string | null;
	denied_by: string | null;
	created_at: string;
	approved_at: string | null;
	has_api_key: boolean;
	_apiKeyRevoked?: boolean;
}

interface ServiceAccountRow {
	id: string;
	name: string;
	description: string | null;
	owner_id: string;
	registered_by: string;
	approved_by: string | null;
	status: Status;
	denial_reason: string | null;
	denied_by: string | null;
	created_at: string;
	approved_at: string | null;
}

const ADMIN = 'usr_000000000000000000000admin';
const now = (offsetMin = 0) => new Date(Date.now() + offsetMin * 60_000).toISOString();

function seedAgent(over: Partial<AgentRow> & Pick<AgentRow, 'id' | 'name' | 'status'>): AgentRow {
	return {
		description: null,
		owner_id: null,
		registered_by: 'self',
		parent_agent_id: null,
		approved_by: null,
		denial_reason: null,
		denied_by: null,
		created_at: now(-60),
		approved_at: null,
		has_api_key: false,
		...over,
	};
}

function seedSa(
	over: Partial<ServiceAccountRow> & Pick<ServiceAccountRow, 'id' | 'name' | 'status'>,
): ServiceAccountRow {
	return {
		description: null,
		owner_id: ADMIN,
		registered_by: ADMIN,
		approved_by: null,
		denial_reason: null,
		denied_by: null,
		created_at: now(-60),
		approved_at: null,
		...over,
	};
}

/** Mutable per-session store. Reset between tests via `resetAgentsStore()`. */
let agents: AgentRow[] = [];
let serviceAccounts: ServiceAccountRow[] = [];
/** Per-actor granted scopes, keyed by actor id (agents + service accounts). */
let actorScopes: Record<string, string[]> = {};

/**
 * The platform permission catalogue (`GET /permissions`).
 *
 * Mirrors the backend's `ALL_PERMISSIONS` (src/jentic_one/admin/core/
 * permissions.py) verbatim — same scope strings, descriptions, and `implies`
 * edges — so dev/tests exercise the real vocabulary, not invented scopes.
 *
 * `org:admin` is marked `grantable_by_caller: false` to reproduce the common
 * real case (a non-admin operator) and exercise the editor's disabled-row
 * gating; the backend additionally *hides* `org:admin` from non-admins, but we
 * keep it visible-but-disabled here so the gating path is observable in dev.
 * Every other entry is grantable, matching an operator who holds those scopes.
 */
const PERMISSION_CATALOGUE: ReadonlyArray<{
	name: string;
	description: string;
	implies: string[];
	grantable_by_caller: boolean;
}> = [
	{
		name: 'org:admin',
		description: 'Full organisation administrator access',
		implies: [
			'agents:read',
			'agents:write',
			'apis:read',
			'audit:read',
			'capabilities:execute',
			'capabilities:read',
			'credentials:read',
			'credentials:write',
			'events:read',
			'events:write',
			'executions:read',
			'jobs:read',
			'jobs:write',
			'service-accounts:read',
			'service-accounts:write',
			'toolkits:read',
			'toolkits:write',
			'users:read',
			'users:write',
		],
		grantable_by_caller: false,
	},
	{
		name: 'capabilities:execute',
		description: 'Execute capabilities via the broker',
		implies: ['apis:read', 'capabilities:read', 'executions:read'],
		grantable_by_caller: true,
	},
	{
		name: 'capabilities:read',
		description: 'Read capability and toolkit metadata',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'toolkits:write',
		description: 'Create, update, and delete toolkits',
		implies: ['toolkits:read'],
		grantable_by_caller: true,
	},
	{
		name: 'toolkits:read',
		description: 'Read toolkit configuration and status',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'users:write',
		description: 'Create, update, and disable users',
		implies: ['users:read'],
		grantable_by_caller: true,
	},
	{
		name: 'users:read',
		description: 'Read user profiles and permissions',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'jobs:write',
		description: 'Cancel and manage async jobs',
		implies: ['jobs:read'],
		grantable_by_caller: true,
	},
	{
		name: 'jobs:read',
		description: 'Read job status and results',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'events:write',
		description: 'Acknowledge and manage platform events',
		implies: ['events:read'],
		grantable_by_caller: true,
	},
	{
		name: 'events:read',
		description: 'Read platform events',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'credentials:write',
		description: 'Create, update, and delete credentials',
		implies: ['credentials:read'],
		grantable_by_caller: true,
	},
	{
		name: 'credentials:read',
		description: 'Read credential metadata',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'apis:read',
		description: 'Read API definitions and metadata',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'executions:read',
		description: 'Read execution records',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'audit:read',
		description: 'Read audit log entries',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'agents:write',
		description: 'Create, update, and delete agents',
		implies: ['agents:read'],
		grantable_by_caller: true,
	},
	{
		name: 'agents:read',
		description: 'Read agent configuration and status',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'service-accounts:write',
		description: 'Create, update, and delete service accounts',
		implies: ['service-accounts:read'],
		grantable_by_caller: true,
	},
	{
		name: 'service-accounts:read',
		description: 'Read service account configuration and status',
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'owner:resources:read',
		description: "Read resources owned by the agent's creator (umbrella)",
		implies: ['owner:agents:read', 'owner:credentials:read', 'owner:toolkits:read'],
		grantable_by_caller: true,
	},
	{
		name: 'owner:credentials:read',
		description: "Read credentials owned by the agent's creator",
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'owner:agents:read',
		description: "Read agents owned by the agent's creator",
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'owner:toolkits:read',
		description: "Read toolkits owned by the agent's creator",
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'owner:access-requests:read',
		description: "Read access requests filed by or for the agent's creator",
		implies: [],
		grantable_by_caller: true,
	},
	{
		name: 'owner:service-accounts:read',
		description: "Read service accounts owned by the agent's creator",
		implies: [],
		grantable_by_caller: true,
	},
];

export function resetAgentsStore(): void {
	agents = [
		seedAgent({ id: 'agnt_pending_1', name: 'inbox-triage-bot', status: 'pending' }),
		seedAgent({ id: 'agnt_pending_2', name: 'release-notes-bot', status: 'pending' }),
		seedAgent({
			id: 'agnt_active_1',
			name: 'support-agent',
			description: 'Handles support tickets end to end.',
			status: 'active',
			approved_by: ADMIN,
			approved_at: now(-30),
		}),
		seedAgent({
			id: 'agnt_disabled_1',
			name: 'legacy-scraper',
			status: 'disabled',
			approved_by: ADMIN,
			approved_at: now(-120),
		}),
		seedAgent({
			id: 'agnt_rejected_1',
			name: 'spammy-bot',
			status: 'rejected',
			denial_reason: 'Unverified publisher.',
			denied_by: ADMIN,
		}),
	];
	serviceAccounts = [
		seedSa({ id: 'sva_pending_1', name: 'nightly-sync', status: 'pending' }),
		seedSa({
			id: 'sva_active_1',
			name: 'metrics-exporter',
			status: 'active',
			approved_by: ADMIN,
			approved_at: now(-30),
		}),
	];
	actorScopes = {
		// Seed realistic grants so the Scopes card renders chips out of the box.
		// `agnt_active_1` carries an approximation of the backend's
		// DEFAULT_AGENT_SCOPES (including `owner:access-requests:read`, now a
		// catalogue-backed scope that renders as a normal editable chip).
		// `legacy:orphaned:read` is a deliberately synthetic scope that is NOT in
		// the catalogue, so it exercises the editor's "preserved scopes not editable
		// here" path (a granted scope absent from /permissions survives a save
		// untouched and is not counted in the picker total).
		agnt_active_1: [
			'capabilities:execute',
			'apis:read',
			'executions:read',
			'owner:resources:read',
			'owner:access-requests:read',
			'legacy:orphaned:read',
		],
		sva_active_1: ['credentials:read'],
	};
}

resetAgentsStore();

function paginate<T extends { status: Status }>(rows: T[], url: URL) {
	const status = url.searchParams.get('status');
	const filtered = status ? rows.filter((r) => r.status === status) : rows;
	return HttpResponse.json({ data: filtered, has_more: false, next_cursor: null });
}

const APPROVE: Record<string, Status> = { pending: 'active' };
const DENY: Record<string, Status> = { pending: 'rejected' };
const DISABLE: Record<string, Status> = { active: 'disabled' };
const ENABLE: Record<string, Status> = { disabled: 'active' };

function transition<T extends { status: Status }>(
	row: T | undefined,
	table: Record<string, Status>,
): { ok: true; row: T } | { ok: false; status: number } {
	if (!row) return { ok: false, status: 404 };
	const next = table[row.status];
	if (!next) return { ok: false, status: 409 };
	row.status = next;
	return { ok: true, row };
}

function genId(prefix: string): string {
	return `${prefix}_${Math.random().toString(16).slice(2, 14)}`;
}

/**
 * The backend's per-scope structural guard (`ScopeStr` in
 * auth/web/schemas/agents.py): each scope is 1–64 chars of `[a-zA-Z0-9_:./-]`.
 * A violation is the only thing the real actor-scope PUT rejects (422, via
 * Pydantic) — see {@link validateScopes}.
 */
const SCOPE_PATTERN = /^[a-zA-Z0-9_:./-]{1,64}$/;

/**
 * The default baseline `AgentService.create` grants when the payload carries
 * no scopes (mirror of `shared/scopes.py` DEFAULT_AGENT_SCOPES). Service
 * accounts get NO baseline — grants only when provided.
 */
const DEFAULT_AGENT_SCOPES_MOCK = [
	'capabilities:execute',
	'capabilities:read',
	'apis:read',
	'catalog:import',
	'executions:read',
	'jobs:read',
	'events:read',
	'owner:resources:read',
	'owner:toolkits:read',
	'owner:agents:read',
	'owner:credentials:read',
	'owner:access-requests:read',
] as const;

/**
 * Validate a replacement scope set the way the real backend actually does.
 *
 * IMPORTANT: `PUT /agents/{id}/scopes` and `PUT /service-accounts/{id}/scopes`
 * do NOT validate scopes against the catalogue and do NOT enforce
 * `grantable_by_caller`. `AgentService.replace_scopes` simply dedupes and writes
 * any string that passes the `ScopeStr` regex. (Catalogue/grantability checks
 * live only on `PUT /users/{id}/permissions`, a different endpoint.) So the only
 * rejection we reproduce here is a malformed scope → 422, matching FastAPI's
 * request-validation response. `grantable_by_caller` is purely a UI hint used to
 * disable rows in the picker, never a server-side gate for actors.
 *
 * The backend also caps the list at 100 entries (`list[ScopeStr] = Field(max_length=100)`),
 * which we mirror so a test that over-grants gets the same 422 the real API would.
 */
function validateScopes(
	requested: string[],
): { ok: true } | { ok: false; status: number; detail: string } {
	if (requested.length > 100) {
		return { ok: false, status: 422, detail: 'Too many scopes (max 100).' };
	}
	for (const s of requested) {
		if (!SCOPE_PATTERN.test(s)) {
			return { ok: false, status: 422, detail: `Invalid scope: ${s}` };
		}
	}
	return { ok: true };
}

/** An actor id known to this store (either roster) — else fall through. */
function findActor(id: string): AgentRow | ServiceAccountRow | undefined {
	return agents.find((a) => a.id === id) ?? serviceAccounts.find((s) => s.id === id);
}

/**
 * Per-actor usage fixture for the detail page's KPI strip + Activity chart
 * (`GET /monitoring/usage?agent_id=…`). Buckets are relative (spread over the
 * trailing week at request time); actors without an entry are genuinely idle.
 * Totals roughly echo the fleet-table fixture in the monitor module so the
 * list and detail read consistently in mocked dev.
 */
const ACTOR_USAGE: Record<
	string,
	{ buckets: { total: number; success: number; failed: number }[] }
> = {
	agnt_active_1: {
		buckets: [
			{ total: 64, success: 64, failed: 0 },
			{ total: 82, success: 81, failed: 1 },
			{ total: 71, success: 70, failed: 1 },
			{ total: 96, success: 95, failed: 1 },
			{ total: 88, success: 87, failed: 1 },
			{ total: 104, success: 103, failed: 1 },
			{ total: 91, success: 90, failed: 1 },
			{ total: 118, success: 117, failed: 1 },
			{ total: 97, success: 96, failed: 1 },
			{ total: 122, success: 121, failed: 1 },
			{ total: 133, success: 131, failed: 2 },
			{ total: 138, success: 137, failed: 1 },
		],
	},
	agnt_disabled_1: {
		buckets: [
			{ total: 22, success: 15, failed: 7 },
			{ total: 18, success: 13, failed: 5 },
			{ total: 15, success: 11, failed: 4 },
			{ total: 12, success: 9, failed: 3 },
			{ total: 10, success: 8, failed: 2 },
			{ total: 8, success: 6, failed: 2 },
			{ total: 6, success: 5, failed: 1 },
			{ total: 3, success: 2, failed: 1 },
			{ total: 2, success: 2, failed: 0 },
		],
	},
	sva_active_1: {
		buckets: [
			{ total: 28, success: 28, failed: 0 },
			{ total: 28, success: 28, failed: 0 },
			{ total: 27, success: 27, failed: 0 },
			{ total: 29, success: 29, failed: 0 },
			{ total: 28, success: 27, failed: 1 },
			{ total: 28, success: 28, failed: 0 },
			{ total: 27, success: 27, failed: 0 },
			{ total: 28, success: 28, failed: 0 },
			{ total: 29, success: 29, failed: 0 },
			{ total: 28, success: 28, failed: 0 },
			{ total: 28, success: 27, failed: 1 },
			{ total: 29, success: 29, failed: 0 },
		],
	},
};

/** One execution feed row (shape mirrors the generated `ExecutionResponse`). */
function executionRow(opts: {
	id: string;
	actorId: string;
	status: 'completed' | 'failed';
	toolkitId: string;
	toolkitName: string;
	operationId: string;
	durationMs: number;
	httpStatus: number;
	minutesAgo: number;
	error?: string;
}) {
	return {
		_links: { self: `/executions/${opts.id}` },
		actor_id: opts.actorId,
		actor_type: opts.actorId.startsWith('sva_') ? 'service_account' : 'agent',
		api: null,
		created_at: now(-opts.minutesAgo),
		duration_ms: opts.durationMs,
		error: opts.error ?? null,
		execution_id: opts.id,
		http_status: opts.httpStatus,
		operation_id: opts.operationId,
		origin: 'api',
		pinned_revisions: null,
		started_at: now(-opts.minutesAgo),
		status: opts.status,
		toolkit_id: opts.toolkitId,
		toolkit_name: opts.toolkitName,
		trace_id: `trace_${opts.id}`,
	};
}

/**
 * Per-actor recent-executions fixture (`GET /executions?actor_id=…`). Only
 * for ids in THIS store — other actor ids fall through to the monitor
 * module's cross-fleet fixture.
 */
const ACTOR_EXECUTIONS: Record<string, ReturnType<typeof executionRow>[]> = {
	agnt_active_1: [
		executionRow({
			id: 'exec_agnt_1',
			actorId: 'agnt_active_1',
			status: 'completed',
			toolkitId: 'github',
			toolkitName: 'github',
			operationId: 'create_issue',
			durationMs: 412,
			httpStatus: 200,
			minutesAgo: 2,
		}),
		executionRow({
			id: 'exec_agnt_2',
			actorId: 'agnt_active_1',
			status: 'failed',
			toolkitId: 'slack',
			toolkitName: 'slack',
			operationId: 'post_message',
			durationMs: 38,
			httpStatus: 403,
			minutesAgo: 9,
			error: 'pbac_denied: scope violation chat:write',
		}),
		executionRow({
			id: 'exec_agnt_3',
			actorId: 'agnt_active_1',
			status: 'completed',
			toolkitId: 'github',
			toolkitName: 'github',
			operationId: 'list_pull_requests',
			durationMs: 220,
			httpStatus: 200,
			minutesAgo: 11,
		}),
		executionRow({
			id: 'exec_agnt_4',
			actorId: 'agnt_active_1',
			status: 'completed',
			toolkitId: 'github',
			toolkitName: 'github',
			operationId: 'get_repo',
			durationMs: 145,
			httpStatus: 200,
			minutesAgo: 34,
		}),
	],
	sva_active_1: [
		executionRow({
			id: 'exec_sva_1',
			actorId: 'sva_active_1',
			status: 'completed',
			toolkitId: 'petstore',
			toolkitName: 'petstore',
			operationId: 'sync_inventory',
			durationMs: 1240,
			httpStatus: 200,
			minutesAgo: 15,
		}),
		executionRow({
			id: 'exec_sva_2',
			actorId: 'sva_active_1',
			status: 'completed',
			toolkitId: 'petstore',
			toolkitName: 'petstore',
			operationId: 'sync_inventory',
			durationMs: 1180,
			httpStatus: 200,
			minutesAgo: 75,
		}),
	],
};

/** One actor-targeted audit row (shape mirrors the generated `AuditResponse`). */
function auditRow(opts: {
	id: string;
	targetId: string;
	action: string;
	minutesAgo: number;
	reason?: string;
}) {
	return {
		action: opts.action,
		actor_id: ADMIN,
		actor_session_id: null,
		actor_type: 'user',
		after: null,
		before: null,
		diff: null,
		id: opts.id,
		ip_address: null,
		job_id: null,
		occurred_at: now(-opts.minutesAgo),
		reason: opts.reason ?? null,
		request_id: null,
		target_id: opts.targetId,
		target_parent_id: null,
		target_type: opts.targetId.startsWith('sva_') ? 'service_account' : 'agent',
		trace_id: null,
		user_agent: null,
	};
}

/**
 * Per-actor audit fixture (`GET /audit?target_type=…&target_id=…`) — the
 * detail console's "Recent changes" panel. Only for ids in THIS store; other
 * targets fall through to the monitor module's org-wide fixture.
 */
const ACTOR_AUDIT: Record<string, ReturnType<typeof auditRow>[]> = {
	agnt_active_1: [
		auditRow({
			id: 'aud_agnt_3',
			targetId: 'agnt_active_1',
			action: 'rotate',
			minutesAgo: 240,
		}),
		auditRow({
			id: 'aud_agnt_2',
			targetId: 'agnt_active_1',
			action: 'approve',
			minutesAgo: 60 * 24 * 6,
		}),
		auditRow({
			id: 'aud_agnt_1',
			targetId: 'agnt_active_1',
			action: 'register',
			minutesAgo: 60 * 24 * 6 + 30,
		}),
	],
	sva_active_1: [
		auditRow({
			id: 'aud_sva_2',
			targetId: 'sva_active_1',
			action: 'approve',
			minutesAgo: 60 * 24 * 12,
		}),
		auditRow({
			id: 'aud_sva_1',
			targetId: 'sva_active_1',
			action: 'create',
			minutesAgo: 60 * 24 * 12,
		}),
	],
};

export const agentsHandlers = [
	// ---- Platform permission catalogue (#615) ----
	http.get('/permissions', () => HttpResponse.json({ data: PERMISSION_CATALOGUE })),

	// ---- Per-actor monitoring enrichment (detail page KPI strip + Activity) ----
	//
	// The detail page reads the same admin-gated monitoring endpoints Monitor
	// does, filtered to one actor. These interceptors answer ONLY for ids that
	// live in THIS module's store and return undefined otherwise, falling
	// through to the monitor module's own `/monitoring/usage` + `/executions`
	// handlers (agents registers before monitor in src/mocks/handlers.ts) —
	// so Monitor's fixtures and tests are untouched.
	http.get('/monitoring/usage', ({ request }) => {
		const actorId = new URL(request.url).searchParams.get('agent_id');
		if (!actorId || !findActor(actorId)) return undefined;
		const usage = ACTOR_USAGE[actorId];
		const nowSec = Math.floor(Date.now() / 60_000) * 60;
		const since = nowSec - 7 * 86_400;
		const buckets = (usage?.buckets ?? []).map((b, i, all) => ({
			// Spread the fixture buckets across the trailing week, newest last
			// (6h grid — what the backend derives for a 7d window).
			ts: nowSec - (all.length - i) * 21_600,
			total: b.total,
			success: b.success,
			failed: b.failed,
			avg_ms: 400,
		}));
		const total = buckets.reduce((sum, b) => sum + b.total, 0);
		const success = buckets.reduce((sum, b) => sum + b.success, 0);
		return HttpResponse.json({
			since,
			until: nowSec,
			bucket_seconds: 21_600,
			group_by: 'api',
			buckets,
			stats: {
				total,
				success,
				failed: total - success,
				pending: 0,
				active_now: 0,
				avg_ms: total ? 400 : 0,
				p50_ms: null,
				p95_ms: null,
			},
			top: [],
		});
	}),
	http.get('/executions', ({ request }) => {
		const actorId = new URL(request.url).searchParams.get('actor_id');
		if (!actorId || !findActor(actorId)) return undefined;
		const rows = ACTOR_EXECUTIONS[actorId] ?? [];
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
	// Actor-scoped audit slice ("Recent changes"): answer only for targets in
	// THIS store, else fall through to the monitor module's org-wide fixture.
	http.get('/audit', ({ request }) => {
		const url = new URL(request.url);
		const targetType = url.searchParams.get('target_type');
		const targetId = url.searchParams.get('target_id');
		if (
			(targetType !== 'agent' && targetType !== 'service_account') ||
			!targetId ||
			!findActor(targetId)
		) {
			return undefined;
		}
		return HttpResponse.json({
			data: ACTOR_AUDIT[targetId] ?? [],
			has_more: false,
			next_cursor: null,
		});
	}),

	// ---- Agents ----
	http.get('/agents', ({ request }) => paginate(agents, new URL(request.url))),
	http.get('/agents/:id', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		return row ? HttpResponse.json(row) : new HttpResponse(null, { status: 404 });
	}),
	http.get('/agents/:id/toolkits', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json({
			data:
				row.id === 'agnt_active_1'
					? [
							{
								id: 'tkb_1',
								agent_id: row.id,
								toolkit_id: 'github',
								bound_at: now(-20),
							},
						]
					: [],
		});
	}),
	// Colon-verb lifecycle. MSW matches the literal `:verb` suffix.
	http.post('/agents/:id\\:approve', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		const res = transition(row, APPROVE);
		if (!res.ok) return new HttpResponse(null, { status: res.status });
		res.row.approved_by = ADMIN;
		res.row.approved_at = now();
		return HttpResponse.json(res.row);
	}),
	http.post('/agents/:id\\:deny', async ({ params, request }) => {
		const body = (await request.json().catch(() => ({}))) as { reason?: string };
		if (!body.reason || !body.reason.trim()) {
			return HttpResponse.json(
				{ detail: [{ loc: ['body', 'reason'], msg: 'Field required', type: 'missing' }] },
				{ status: 422 },
			);
		}
		const row = agents.find((a) => a.id === params.id);
		const res = transition(row, DENY);
		if (!res.ok) return new HttpResponse(null, { status: res.status });
		res.row.denial_reason = body.reason;
		res.row.denied_by = ADMIN;
		return HttpResponse.json(res.row);
	}),
	http.post('/agents/:id\\:disable', ({ params }) => {
		const res = transition(
			agents.find((a) => a.id === params.id),
			DISABLE,
		);
		return new HttpResponse(null, { status: res.ok ? 204 : res.status });
	}),
	http.post('/agents/:id\\:enable', ({ params }) => {
		const res = transition(
			agents.find((a) => a.id === params.id),
			ENABLE,
		);
		return new HttpResponse(null, { status: res.ok ? 204 : res.status });
	}),
	http.delete('/agents/:id', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.status = 'archived';
		return new HttpResponse(null, { status: 204 });
	}),
	// Manual agent creation.
	http.post('/agents', async ({ request }) => {
		const body = (await request.json().catch(() => ({}))) as {
			name?: string;
			description?: string | null;
			scopes?: string[] | null;
		};
		// Validate BEFORE mutating the store — the real backend rejects via
		// Pydantic before anything is created (no phantom row on a 422).
		if (Array.isArray(body.scopes) && body.scopes.length > 0) {
			const check = validateScopes(body.scopes);
			if (!check.ok) {
				return HttpResponse.json({ detail: check.detail }, { status: check.status });
			}
		}
		const row = seedAgent({
			id: genId('agnt'),
			name: body.name ?? 'unnamed',
			description: body.description ?? null,
			status: 'active',
			created_at: now(),
		});
		agents.unshift(row);
		// `AgentService.create` grants the requested scopes verbatim, or the
		// DEFAULT_AGENT_SCOPES baseline when the payload carries none — a fresh
		// manual agent never has an empty Scopes card (shared/scopes.py).
		actorScopes[row.id] =
			Array.isArray(body.scopes) && body.scopes.length > 0
				? [...new Set(body.scopes)]
				: [...DEFAULT_AGENT_SCOPES_MOCK];
		return HttpResponse.json(row, { status: 201 });
	}),
	// Partial in-place edit — name / description / owner_id. Mirrors
	// `AgentService.update_agent`: archived rows reject with 409
	// (InvalidTransitionError), and set fields pass through un-trimmed.
	http.patch('/agents/:id', async ({ params, request }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.status === 'archived') return new HttpResponse(null, { status: 409 });
		const body = (await request.json().catch(() => ({}))) as {
			name?: string | null;
			description?: string | null;
			owner_id?: string | null;
		};
		if (typeof body.name === 'string') row.name = body.name;
		if (body.description !== undefined) row.description = body.description;
		if (body.owner_id !== undefined) row.owner_id = body.owner_id;
		return HttpResponse.json(row);
	}),
	// Generate API key for an agent.
	http.post('/agents/:id\\:generate-api-key', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.status !== 'active') return new HttpResponse(null, { status: 409 });
		row.has_api_key = true;
		row._apiKeyRevoked = false;
		return HttpResponse.json({ key: `jak_mock_${genId('key')}` });
	}),
	// Revoke API key for an agent.
	http.post('/agents/:id\\:revoke-api-key', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.status !== 'active') return new HttpResponse(null, { status: 409 });
		if (!row.has_api_key) return new HttpResponse(null, { status: 409 });
		row.has_api_key = false;
		row._apiKeyRevoked = true;
		return new HttpResponse(null, { status: 204 });
	}),
	// Get API key info for an agent.
	http.get('/agents/:id/api-key', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (!row.has_api_key && !row._apiKeyRevoked) return HttpResponse.json(null);
		return HttpResponse.json({
			id: `agc_${params.id}`,
			status: row.has_api_key ? 'active' : 'revoked',
			created_at: row.created_at,
			rotated_at: row.has_api_key ? null : now(),
			created_by: ADMIN,
		});
	}),
	// Get API key history for an agent.
	http.get('/agents/:id/api-key/history', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const data = [];
		if (row.has_api_key || row._apiKeyRevoked) {
			data.push({
				id: `aud_${params.id}_1`,
				action: 'rotate',
				reason: 'api_key_rotated',
				actor_id: ADMIN,
				occurred_at: row.created_at,
			});
		}
		if (row._apiKeyRevoked) {
			data.unshift({
				id: `aud_${params.id}_2`,
				action: 'revoke',
				reason: 'api_key_revoked',
				actor_id: ADMIN,
				occurred_at: now(),
			});
		}
		return HttpResponse.json({ data });
	}),
	// ---- Agent scopes (#615) ----
	http.get('/agents/:id/scopes', ({ params }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json({ scopes: actorScopes[row.id] ?? [] });
	}),
	http.put('/agents/:id/scopes', async ({ params, request }) => {
		const row = agents.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json().catch(() => ({}))) as { scopes?: string[] };
		const requested = Array.isArray(body.scopes) ? body.scopes : [];
		const check = validateScopes(requested);
		if (!check.ok) {
			return HttpResponse.json({ detail: check.detail }, { status: check.status });
		}
		actorScopes[row.id] = [...new Set(requested)];
		return HttpResponse.json({ scopes: actorScopes[row.id] });
	}),
	// Dynamic client registration → creates a pending agent row.
	http.post('/register', async ({ request }) => {
		const body = (await request.json().catch(() => ({}))) as { client_name?: string };
		const id = genId('agnt');
		agents.unshift(
			seedAgent({
				id,
				name: body.client_name ?? 'unnamed-agent',
				status: 'pending',
				created_at: now(),
			}),
		);
		return HttpResponse.json(
			{
				client_id: id,
				registration_access_token: genId('rat'),
				registration_client_uri: `/register/${id}`,
				status: 'pending',
				grant_types: ['urn:ietf:params:oauth:grant-type:jwt-bearer'],
				token_endpoint_auth_method: 'private_key_jwt',
			},
			{ status: 201 },
		);
	}),

	// ---- Service accounts ----
	http.get('/service-accounts', ({ request }) => paginate(serviceAccounts, new URL(request.url))),
	http.post('/service-accounts', async ({ request }) => {
		const body = (await request.json().catch(() => ({}))) as {
			name?: string;
			description?: string | null;
			scopes?: string[] | null;
		};
		// Validate BEFORE mutating the store — the real backend rejects via
		// Pydantic before anything is created (no phantom row on a 422).
		if (Array.isArray(body.scopes) && body.scopes.length > 0) {
			const check = validateScopes(body.scopes);
			if (!check.ok) {
				return HttpResponse.json({ detail: check.detail }, { status: check.status });
			}
		}
		const row = seedSa({
			id: genId('sva'),
			name: body.name ?? 'unnamed',
			description: body.description ?? null,
			// ServiceAccountService.create approves inside the create
			// transaction — a fresh SA is active, never pending.
			status: 'active',
			created_at: now(),
			approved_by: ADMIN,
			approved_at: now(),
		});
		serviceAccounts.unshift(row);
		// Unlike agents, SAs get no default baseline — grants only when provided
		// (ServiceAccountService.create).
		if (Array.isArray(body.scopes) && body.scopes.length > 0) {
			actorScopes[row.id] = [...new Set(body.scopes)];
		}
		return HttpResponse.json(row, { status: 201 });
	}),
	http.get('/service-accounts/:id', ({ params }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		return row ? HttpResponse.json(row) : new HttpResponse(null, { status: 404 });
	}),
	http.post('/service-accounts/:id\\:approve', ({ params }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		const res = transition(row, APPROVE);
		if (!res.ok) return new HttpResponse(null, { status: res.status });
		res.row.approved_by = ADMIN;
		res.row.approved_at = now();
		return HttpResponse.json(res.row);
	}),
	http.post('/service-accounts/:id\\:deny', async ({ params, request }) => {
		const body = (await request.json().catch(() => ({}))) as { reason?: string };
		if (!body.reason || !body.reason.trim()) {
			return HttpResponse.json(
				{ detail: [{ loc: ['body', 'reason'], msg: 'Field required', type: 'missing' }] },
				{ status: 422 },
			);
		}
		const row = serviceAccounts.find((a) => a.id === params.id);
		const res = transition(row, DENY);
		if (!res.ok) return new HttpResponse(null, { status: res.status });
		res.row.denial_reason = body.reason;
		res.row.denied_by = ADMIN;
		return HttpResponse.json(res.row);
	}),
	http.post('/service-accounts/:id\\:disable', ({ params }) => {
		const res = transition(
			serviceAccounts.find((a) => a.id === params.id),
			DISABLE,
		);
		return new HttpResponse(null, { status: res.ok ? 204 : res.status });
	}),
	http.post('/service-accounts/:id\\:enable', ({ params }) => {
		const res = transition(
			serviceAccounts.find((a) => a.id === params.id),
			ENABLE,
		);
		return new HttpResponse(null, { status: res.ok ? 204 : res.status });
	}),
	http.delete('/service-accounts/:id', ({ params }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		row.status = 'archived';
		return new HttpResponse(null, { status: 204 });
	}),
	// Generate API key for a service account.
	http.post('/service-accounts/:id\\:generate-api-key', ({ params }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		if (row.status !== 'active') return new HttpResponse(null, { status: 409 });
		return HttpResponse.json({ key: `jak_mock_${genId('key')}` });
	}),
	// ---- Service-account scopes (#615) ----
	http.get('/service-accounts/:id/scopes', ({ params }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json({ scopes: actorScopes[row.id] ?? [] });
	}),
	http.put('/service-accounts/:id/scopes', async ({ params, request }) => {
		const row = serviceAccounts.find((a) => a.id === params.id);
		if (!row) return new HttpResponse(null, { status: 404 });
		const body = (await request.json().catch(() => ({}))) as { scopes?: string[] };
		const requested = Array.isArray(body.scopes) ? body.scopes : [];
		const check = validateScopes(requested);
		if (!check.ok) {
			return HttpResponse.json({ detail: check.detail }, { status: check.status });
		}
		actorScopes[row.id] = [...new Set(requested)];
		return HttpResponse.json({ scopes: actorScopes[row.id] });
	}),
];
