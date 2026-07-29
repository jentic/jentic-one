import { http, HttpResponse } from 'msw';

/**
 * Toolkits module MSW handlers (Mode A — mocked dev/e2e). Registered in
 * `src/mocks/handlers.ts` with one additive spread line. These mirror the REAL
 * `control` toolkit contract (verified against the live `/openapi.json`), so
 * the mocked surface matches what the repository tier expects from the backend.
 */
type MockToolkit = {
	toolkit_id: string;
	name: string;
	description: string | null;
	active: boolean;
	key_count: number;
	credential_count: number;
	permissions: Array<Record<string, unknown>>;
	created_at: string;
	created_by?: string | null;
	updated_at: string | null;
};

const now = () => new Date().toISOString();

const toolkits: MockToolkit[] = [
	{
		toolkit_id: 'tk_demo_github',
		name: 'GitHub Tools',
		description: 'Issues, PRs, and repo automation for the support agent.',
		active: true,
		key_count: 2,
		credential_count: 1,
		permissions: [],
		created_at: '2026-05-01T10:00:00Z',
		created_by: 'admin@local',
		updated_at: null,
	},
	{
		toolkit_id: 'tk_demo_billing',
		name: 'Billing (suspended)',
		description: 'Stripe + internal billing. Suspended pending review.',
		active: false,
		key_count: 1,
		credential_count: 2,
		permissions: [],
		created_at: '2026-04-12T08:30:00Z',
		created_by: 'admin@local',
		updated_at: '2026-06-01T12:00:00Z',
	},
];

const keysByToolkit: Record<string, Array<Record<string, unknown>>> = {
	tk_demo_github: [
		{
			key_id: 'key_1',
			toolkit_id: 'tk_demo_github',
			label: 'CI runner',
			key_preview: 'jntc_live_ab12…',
			revoked: false,
			allowed_ips: null,
			last_used_at: '2026-06-18T09:00:00Z',
			created_at: '2026-05-01T10:05:00Z',
		},
	],
};

const bindingsByToolkit: Record<string, Array<Record<string, unknown>>> = {
	tk_demo_github: [
		{
			toolkit_id: 'tk_demo_github',
			credential_id: 'cred_gh_1',
			label: 'GitHub PAT',
			api_name: 'GitHub',
			api_vendor: 'github',
			credential_type: 'api_key',
			bound_at: '2026-05-01T10:10:00Z',
			warnings: [],
			permissions: [
				{ effect: 'allow', methods: ['GET'], path: '/repos/.*', _system: false },
				{
					effect: 'deny',
					methods: null,
					path: '/admin/.*',
					_system: true,
					_comment: 'system safety',
				},
			],
		},
	],
};

/**
 * Agents bound to each toolkit (reverse lookup, served by
 * `GET /toolkits/:id/agents`). Link/unlink in tests mutate this so the
 * Bound Agents section reflects changes after a mutation.
 */
const agentsByToolkit: Record<string, Array<Record<string, unknown>>> = {
	tk_demo_github: [
		{
			agent_id: 'agt_support_bot',
			agent_name: 'Support Bot',
			status: 'active',
			bound_at: '2026-05-02T09:00:00Z',
		},
	],
};

/** Workspace agents — the candidate list for the "Link agent" picker. */
const agents: Array<Record<string, unknown>> = [
	{
		id: 'agt_support_bot',
		name: 'Support Bot',
		status: 'active',
		registered_by: 'admin@local',
		created_at: '2026-04-01T09:00:00Z',
	},
	{
		id: 'agt_billing_bot',
		name: 'Billing Bot',
		status: 'active',
		registered_by: 'admin@local',
		created_at: '2026-04-05T09:00:00Z',
	},
	{
		id: 'agt_pending_bot',
		name: 'Pending Bot',
		status: 'pending',
		registered_by: 'admin@local',
		created_at: '2026-04-08T09:00:00Z',
	},
];

const find = (id: string) => toolkits.find((t) => t.toolkit_id === id);

/**
 * Zero-rules bind warning — mirrors the backend's `BindingWarningSchema`
 * emitted when a binding lands with no permission rules (the broker denies by
 * default until rules are added).
 */
const zeroRulesWarning = (credentialId: string) => ({
	code: 'no_permission_rules',
	credential_id: credentialId,
	message:
		'This binding has no permission rules — the broker denies every request by default. Add allow rules to open access.',
});

/**
 * Per-toolkit observability fixtures for the detail page's KPI strip +
 * Activity tab. The github toolkit is busy; the suspended billing toolkit is
 * quiet (zero executions), which exercises the empty chart/feed states.
 */
const usageTrendByToolkit: Record<string, number[]> = {
	tk_demo_github: [64, 88, 71, 120, 104, 141, 126],
	tk_demo_billing: [],
};

const executionsByToolkit: Record<string, Array<Record<string, unknown>>> = {
	tk_demo_github: [
		{
			execution_id: 'tkexec_1',
			toolkit_id: 'tk_demo_github',
			trace_id: 'trace_tk_1',
			status: 'completed',
			operation_id: 'github.create_issue',
			api: { vendor: 'github', name: 'github-api' },
			actor_id: 'agt_support_bot',
			actor_type: 'agent',
			http_status: 201,
			duration_ms: 310,
			error: null,
			origin: 'agent',
			started_at: new Date(Date.now() - 4 * 60_000).toISOString(),
			created_at: new Date(Date.now() - 4 * 60_000).toISOString(),
			_links: { self: '/executions/tkexec_1' },
		},
		{
			execution_id: 'tkexec_2',
			toolkit_id: 'tk_demo_github',
			trace_id: 'trace_tk_2',
			status: 'failed',
			operation_id: 'github.delete_repo',
			api: { vendor: 'github', name: 'github-api' },
			actor_id: 'agt_support_bot',
			actor_type: 'agent',
			http_status: 403,
			duration_ms: 22,
			error: 'Denied by permission rule (deny /admin/.*).',
			origin: 'agent',
			started_at: new Date(Date.now() - 18 * 60_000).toISOString(),
			created_at: new Date(Date.now() - 18 * 60_000).toISOString(),
			_links: { self: '/executions/tkexec_2' },
		},
		{
			execution_id: 'tkexec_3',
			toolkit_id: 'tk_demo_github',
			trace_id: 'trace_tk_3',
			status: 'completed',
			operation_id: 'github.list_prs',
			api: { vendor: 'github', name: 'github-api' },
			actor_id: 'agt_support_bot',
			actor_type: 'agent',
			http_status: 200,
			duration_ms: 180,
			error: null,
			origin: 'agent',
			started_at: new Date(Date.now() - 22 * 60_000).toISOString(),
			created_at: new Date(Date.now() - 22 * 60_000).toISOString(),
			_links: { self: '/executions/tkexec_3' },
		},
	],
};

export const toolkitsHandlers = [
	http.get('/toolkits', ({ request }) => {
		const url = new URL(request.url);
		const limit = Math.max(1, Number(url.searchParams.get('limit') ?? 50));
		const cursor = url.searchParams.get('cursor');
		const start = cursor ? Number(cursor) : 0;
		const page = toolkits.slice(start, start + limit);
		const nextStart = start + limit;
		const hasMore = nextStart < toolkits.length;
		return HttpResponse.json({
			data: page,
			has_more: hasMore,
			next_cursor: hasMore ? String(nextStart) : null,
		});
	}),

	http.post('/toolkits', async ({ request }) => {
		const body = (await request.json()) as {
			name: string;
			description?: string | null;
			credential_ids?: string[] | null;
		};
		const credentialIds = body.credential_ids ?? [];
		const toolkit: MockToolkit = {
			toolkit_id: `tk_${Math.random().toString(36).slice(2, 8)}`,
			name: body.name,
			description: body.description ?? null,
			active: true,
			key_count: 1,
			credential_count: credentialIds.length,
			permissions: [],
			created_at: now(),
			created_by: 'admin@local',
			updated_at: null,
		};
		toolkits.unshift(toolkit);
		// Inline binds land with zero rules — mirror the backend's warning.
		if (credentialIds.length > 0) {
			bindingsByToolkit[toolkit.toolkit_id] = credentialIds.map((credentialId) => ({
				toolkit_id: toolkit.toolkit_id,
				credential_id: credentialId,
				label: credentialId,
				api_name: null,
				api_vendor: null,
				credential_type: null,
				bound_at: now(),
				warnings: [zeroRulesWarning(credentialId)],
				permissions: [],
			}));
		}
		return HttpResponse.json({ toolkit, api_key: 'jntc_live_mockplaintextkey_show_once' });
	}),

	http.get('/toolkits/:toolkitId', ({ params }) => {
		const toolkit = find(params.toolkitId as string);
		if (!toolkit) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(toolkit);
	}),

	http.patch('/toolkits/:toolkitId', async ({ params, request }) => {
		const toolkit = find(params.toolkitId as string);
		if (!toolkit) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as Partial<MockToolkit>;
		if (body.name != null) toolkit.name = body.name;
		if (body.description !== undefined) toolkit.description = body.description ?? null;
		if (body.active != null) toolkit.active = body.active;
		toolkit.updated_at = now();
		return HttpResponse.json(toolkit);
	}),

	http.delete('/toolkits/:toolkitId', ({ params }) => {
		const toolkitId = params.toolkitId as string;
		const idx = toolkits.findIndex((t) => t.toolkit_id === toolkitId);
		if (idx < 0) return new HttpResponse(null, { status: 404 });
		toolkits.splice(idx, 1);
		// Mirror the backend cascade so reverse-lookup endpoints return 404 /
		// empty after the delete (keys, bindings, and reverse agent grants).
		delete keysByToolkit[toolkitId];
		delete bindingsByToolkit[toolkitId];
		delete agentsByToolkit[toolkitId];
		return new HttpResponse(null, { status: 204 });
	}),

	http.get('/toolkits/:toolkitId/keys', ({ params }) =>
		HttpResponse.json({
			data: keysByToolkit[params.toolkitId as string] ?? [],
			has_more: false,
		}),
	),

	http.post('/toolkits/:toolkitId/keys', async ({ params, request }) => {
		const toolkitId = params.toolkitId as string;
		const body = (await request.json()) as {
			label?: string | null;
			allowed_ips?: string[] | null;
		};
		const key = {
			key_id: `key_${Math.random().toString(36).slice(2, 8)}`,
			toolkit_id: toolkitId,
			label: body.label ?? null,
			key_preview: 'jntc_live_new…',
			revoked: false,
			allowed_ips: body.allowed_ips?.length ? body.allowed_ips : null,
			last_used_at: null,
			created_at: now(),
		};
		keysByToolkit[toolkitId] = [...(keysByToolkit[toolkitId] ?? []), key];
		return HttpResponse.json({ key, api_key: 'jntc_live_freshmockplaintext_show_once' });
	}),

	http.patch('/toolkits/:toolkitId/keys/:keyId', async ({ params, request }) => {
		const list = keysByToolkit[params.toolkitId as string] ?? [];
		const key = list.find((k) => k.key_id === params.keyId);
		if (!key) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as {
			revoked?: boolean | null;
			label?: string | null;
			allowed_ips?: string[] | null;
		};
		if (body.revoked != null) key.revoked = body.revoked;
		if (body.label !== undefined) key.label = body.label ?? null;
		if (body.allowed_ips !== undefined)
			key.allowed_ips = body.allowed_ips?.length ? body.allowed_ips : null;
		return HttpResponse.json(key);
	}),

	http.delete('/toolkits/:toolkitId/keys/:keyId', ({ params }) => {
		const toolkitId = params.toolkitId as string;
		keysByToolkit[toolkitId] = (keysByToolkit[toolkitId] ?? []).filter(
			(k) => k.key_id !== params.keyId,
		);
		return new HttpResponse(null, { status: 204 });
	}),

	http.get('/toolkits/:toolkitId/credentials', ({ params }) =>
		HttpResponse.json({
			data: bindingsByToolkit[params.toolkitId as string] ?? [],
			has_more: false,
		}),
	),

	http.post('/toolkits/:toolkitId/credentials', async ({ params, request }) => {
		const toolkitId = params.toolkitId as string;
		const body = (await request.json()) as { credential_id: string };
		const binding = {
			toolkit_id: toolkitId,
			credential_id: body.credential_id,
			label: body.credential_id,
			api_name: null,
			api_vendor: null,
			credential_type: null,
			bound_at: now(),
			// A fresh bind has zero rules → the backend flags it (broker denies
			// by default). The warning persists on the list response too.
			warnings: [zeroRulesWarning(body.credential_id)],
			permissions: [],
		};
		bindingsByToolkit[toolkitId] = [...(bindingsByToolkit[toolkitId] ?? []), binding];
		return HttpResponse.json(binding);
	}),

	http.delete('/toolkits/:toolkitId/credentials/:credentialId', ({ params }) => {
		const toolkitId = params.toolkitId as string;
		bindingsByToolkit[toolkitId] = (bindingsByToolkit[toolkitId] ?? []).filter(
			(b) => b.credential_id !== params.credentialId,
		);
		return new HttpResponse(null, { status: 204 });
	}),

	http.get('/toolkits/:toolkitId/credentials/:credentialId/permissions', ({ params }) => {
		const list = bindingsByToolkit[params.toolkitId as string] ?? [];
		const binding = list.find((b) => b.credential_id === params.credentialId);
		return HttpResponse.json({ data: (binding?.permissions as unknown[]) ?? [] });
	}),

	http.put(
		'/toolkits/:toolkitId/credentials/:credentialId/permissions',
		async ({ params, request }) => {
			const list = bindingsByToolkit[params.toolkitId as string] ?? [];
			const binding = list.find((b) => b.credential_id === params.credentialId);
			const rules = (await request.json()) as Array<Record<string, unknown>>;
			const withSystem = [
				...rules,
				{
					effect: 'deny',
					methods: null,
					path: '/admin/.*',
					_system: true,
					_comment: 'system safety',
				},
			];
			if (binding) {
				binding.permissions = withSystem;
				// Authoring rules resolves the zero-rules warning.
				if (rules.length > 0) binding.warnings = [];
			}
			return HttpResponse.json({ data: withSystem });
		},
	),

	// Broker dry-run (`POST …/permissions:test`) — evaluates the binding's rule
	// list in order, the way the broker's vendor-pooled evaluation does, without
	// calling upstream. The colon is escaped so path-to-regexp reads a literal
	// `permissions:test` segment rather than a `:test` param.
	http.post(
		'/toolkits/:toolkitId/credentials/:credentialId/permissions\\:test',
		async ({ params, request }) => {
			const list = bindingsByToolkit[params.toolkitId as string] ?? [];
			const binding = list.find((b) => b.credential_id === params.credentialId);
			if (!binding) return new HttpResponse(null, { status: 404 });
			const body = (await request.json()) as { method: string; path: string };
			const rules = (binding.permissions as Array<Record<string, unknown>>) ?? [];
			const method = body.method.toUpperCase();
			for (let i = 0; i < rules.length; i++) {
				const rule = rules[i];
				const methods = rule.methods as string[] | null;
				if (methods && !methods.map((m) => m.toUpperCase()).includes(method)) continue;
				let matches = false;
				try {
					matches = new RegExp(`^${rule.path as string}$`).test(body.path);
				} catch {
					matches = false;
				}
				if (!matches) continue;
				return HttpResponse.json({
					allowed: rule.effect === 'allow',
					matched: true,
					effect: rule.effect,
					rule_index: i,
					is_system: Boolean(rule._system),
					credential_id: params.credentialId,
				});
			}
			// No rule matched → default deny.
			return HttpResponse.json({
				allowed: false,
				matched: false,
				effect: null,
				rule_index: null,
				is_system: null,
				credential_id: null,
			});
		},
	),

	// --- Agent bindings (reverse lookup + agent-side link/unlink) ---

	http.get('/toolkits/:toolkitId/agents', ({ params }) =>
		HttpResponse.json({
			data: agentsByToolkit[params.toolkitId as string] ?? [],
			has_more: false,
			next_cursor: null,
		}),
	),

	// Link: agent-side bind (POST /agents/:agentId/toolkits { toolkit_id }).
	http.post('/agents/:agentId/toolkits', async ({ params, request }) => {
		const agentId = params.agentId as string;
		const body = (await request.json()) as { toolkit_id: string };
		const toolkitId = body.toolkit_id;
		const agent = agents.find((a) => a.id === agentId);
		const list = agentsByToolkit[toolkitId] ?? [];
		if (agent && !list.some((a) => a.agent_id === agentId)) {
			agentsByToolkit[toolkitId] = [
				...list,
				{
					agent_id: agentId,
					agent_name: agent.name,
					status: agent.status,
					bound_at: now(),
				},
			];
		}
		return HttpResponse.json({
			agent_id: agentId,
			toolkit_id: toolkitId,
			bound_at: now(),
		});
	}),

	// Unlink: agent-side unbind (DELETE /agents/:agentId/toolkits/:toolkitId).
	http.delete('/agents/:agentId/toolkits/:toolkitId', ({ params }) => {
		const { agentId, toolkitId } = params as { agentId: string; toolkitId: string };
		agentsByToolkit[toolkitId] = (agentsByToolkit[toolkitId] ?? []).filter(
			(a) => a.agent_id !== agentId,
		);
		return new HttpResponse(null, { status: 204 });
	}),

	http.get('/audit', ({ request }) => {
		const url = new URL(request.url);
		const targetType = url.searchParams.get('target_type');
		const targetId = url.searchParams.get('target_id');
		if (targetType !== 'toolkit') {
			return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
		}
		const data = [
			{
				id: 'aud_2',
				occurred_at: '2026-06-01T12:00:00Z',
				action: 'update',
				target_type: 'toolkit',
				target_id: targetId ?? 'tk_demo_github',
				actor_type: 'user',
				actor_id: 'admin@local',
				reason: 'suspended pending review',
			},
			{
				id: 'aud_1',
				occurred_at: '2026-05-01T10:00:00Z',
				action: 'create',
				target_type: 'toolkit',
				target_id: targetId ?? 'tk_demo_github',
				actor_type: 'user',
				actor_id: 'admin@local',
				reason: null,
			},
		];
		return HttpResponse.json({ data, has_more: false, next_cursor: null });
	}),

	// --- Toolkit-scoped observability lenses -------------------------------
	//
	// These two handlers answer ONLY the toolkit-scoped variants of the shared
	// monitoring endpoints (`?toolkit_id=…`, or `group_by=toolkit` for the list
	// page's sparklines). Anything else falls through (returns undefined) to
	// the dashboard/monitor fixtures registered later — MSW is first-match-wins
	// and the toolkits module registers first.

	http.get('/monitoring/usage', ({ request }) => {
		const url = new URL(request.url);
		const toolkitId = url.searchParams.get('toolkit_id');
		const groupBy = url.searchParams.get('group_by');
		// The sparkline query is `group_by=toolkit&top_limit=50` (the repository
		// pins top_limit to the max). The dashboard's Top-usage toolkit lens uses
		// top_limit=5, so it keeps falling through to its own fixtures.
		const isSparklineQuery =
			groupBy === 'toolkit' && url.searchParams.get('top_limit') === '50';
		if (!toolkitId && !isSparklineQuery) return undefined;

		const nowSec = Math.floor(Date.now() / 60_000) * 60;
		const untilParam = url.searchParams.get('until');
		const until = untilParam != null ? Number(untilParam) : nowSec;
		const sinceParam = url.searchParams.get('since');
		const since = sinceParam != null ? Number(sinceParam) : until - 7 * 86_400;

		const trendFor = (id: string) => usageTrendByToolkit[id] ?? [];
		const statsOf = (trend: number[]) => {
			const total = trend.reduce((sum, v) => sum + v, 0);
			const failed = Math.round(total * 0.024);
			return { total, success: total - failed, failed };
		};

		const bucketsFor = (trend: number[]) =>
			trend.map((total, i) => {
				const { failed } = statsOf([total]);
				return {
					ts: until - (trend.length - i) * 86_400,
					total,
					success: total - failed,
					failed,
					avg_ms: 420,
				};
			});

		if (toolkitId) {
			const trend = trendFor(toolkitId);
			const { total, success, failed } = statsOf(trend);
			return HttpResponse.json({
				since,
				until,
				bucket_seconds: 86_400,
				group_by: groupBy ?? 'api',
				stats: {
					total,
					success,
					failed,
					pending: 0,
					avg_ms: total ? 420 : 0,
					p50_ms: total ? 310 : null,
					p95_ms: total ? 412 : null,
					active_now: 0,
				},
				buckets: bucketsFor(trend),
				top: [],
			});
		}

		// group_by=toolkit — the list page's sparkline query. Top rows keyed by
		// the seeded toolkit ids so cards can join on toolkit_id.
		const top = toolkits
			.map((t) => {
				const trend = trendFor(t.toolkit_id);
				const { total, success, failed } = statsOf(trend);
				return {
					key: t.toolkit_id,
					label: t.toolkit_id,
					total,
					success,
					failed,
					avg_ms: total ? 420 : 0,
					trend,
				};
			})
			.filter((row) => row.total > 0)
			.sort((a, b) => b.total - a.total);
		const total = top.reduce((sum, r) => sum + r.total, 0);
		const success = top.reduce((sum, r) => sum + r.success, 0);
		const failed = top.reduce((sum, r) => sum + r.failed, 0);
		return HttpResponse.json({
			since,
			until,
			bucket_seconds: 86_400,
			group_by: 'toolkit',
			stats: {
				total,
				success,
				failed,
				pending: 0,
				avg_ms: total ? 420 : 0,
				p50_ms: total ? 310 : null,
				p95_ms: total ? 412 : null,
				active_now: 0,
			},
			buckets: [],
			top,
		});
	}),

	http.get('/executions', ({ request }) => {
		const url = new URL(request.url);
		const toolkitId = url.searchParams.get('toolkit_id');
		if (!toolkitId) return undefined; // Monitor's org-wide fixtures answer.
		const limit = Math.max(1, Number(url.searchParams.get('limit') ?? 25));
		const rows = (executionsByToolkit[toolkitId] ?? []).slice(0, limit);
		return HttpResponse.json({ data: rows, has_more: false, next_cursor: null });
	}),
];
