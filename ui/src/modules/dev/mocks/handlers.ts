/**
 * MSW handlers for the dev-only access-request showcase. They make every
 * showcase request (and the fulfilment wizard's create/amend/decide calls) work
 * without a live backend, so `/app/dev/access-requests` is fully interactive
 * with `VITE_ENABLE_MSW=1`.
 *
 * DEV-ONLY: registered in src/mocks/handlers.ts behind an `import.meta.env.DEV`
 * guard; excluded from production builds.
 */
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { SHOWCASE_BY_ID } from '@/modules/dev/fixtures';
import type { AccessRequest } from '@/shared/lib';

// A mutable copy so decide/amend during a demo session are reflected on re-open.
const state: Record<string, AccessRequest> = structuredClone(SHOWCASE_BY_ID);

let seq = 0;
const nextId = (prefix: string) => `${prefix}_dev${(++seq).toString().padStart(4, '0')}`;

/** The caller can always fulfil in the showcase (no reviewer gating). */
function withEvaluation(req: AccessRequest): AccessRequest {
	return { ...req, evaluation: { can_fulfill: true, checks: [] } };
}

export const devAccessRequestHandlers = [
	// List — powers any list view; returns the showcase set.
	http.get('/access-requests', () =>
		HttpResponse.json({
			data: Object.values(state),
			has_more: false,
			next_cursor: null,
		}),
	),

	// Get one (the dialogs fetch this for items + evaluation).
	http.get('/access-requests/:id', ({ params }) => {
		const req = state[String(params.id)];
		if (!req) return new HttpResponse(null, { status: 404 });
		return HttpResponse.json(withEvaluation(req));
	}),

	// Amend — the wizard writes resolved ids/rules onto pending items.
	http.post('/access-requests/:id\\:amend', async ({ params, request }) => {
		const req = state[String(params.id)];
		if (!req) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as {
			items: { item_id: string; resource_id?: string; to_id?: string; rules?: unknown[] }[];
		};
		for (const amend of body.items) {
			const it = req.items.find((i) => i.id === amend.item_id);
			if (!it) continue;
			if (amend.resource_id != null) it.resource_id = amend.resource_id;
			if (amend.to_id != null) {
				it.to_id = amend.to_id;
				it.to_type = 'toolkit';
			}
			if (amend.rules != null)
				it.rules = amend.rules as AccessRequest['items'][number]['rules'];
		}
		return HttpResponse.json(withEvaluation(req));
	}),

	// Decide — approve/deny items; recompute the aggregate status.
	http.post('/access-requests/:id\\:decide', async ({ params, request }) => {
		const req = state[String(params.id)];
		if (!req) return new HttpResponse(null, { status: 404 });
		const body = (await request.json()) as {
			items: { item_id: string; decision: string; decision_reason?: string }[];
		};
		for (const d of body.items) {
			const it = req.items.find((i) => i.id === d.item_id);
			if (it) {
				it.status = d.decision;
				it.decided_by = 'usr_dev_owner';
				it.decision_reason = d.decision_reason ?? null;
			}
		}
		const statuses = req.items.map((i) => i.status);
		req.status = statuses.every((s) => s === 'approved')
			? 'approved'
			: statuses.some((s) => s === 'approved')
				? 'partially_approved'
				: 'denied';
		return HttpResponse.json(withEvaluation(req));
	}),

	// ── Wizard fulfilment endpoints ───────────────────────────────────────────
	// Create toolkit.
	http.post('/toolkits', async ({ request }) => {
		const body = (await request.json()) as { name: string };
		const id = nextId('tk');
		return HttpResponse.json(
			{ toolkit: { toolkit_id: id, name: body.name, active: true }, api_key: 'demo_key' },
			{ status: 201 },
		);
	}),

	// Create credential (the reused CreateCredentialDialog posts here).
	http.post('/credentials', async ({ request }) => {
		const body = (await request.json()) as { type: string; provider?: string; name: string };
		const id = nextId('cred');
		return HttpResponse.json(
			{
				credential: {
					credential_id: id,
					type: body.type,
					name: body.name,
					provider: body.provider ?? 'static',
					active: true,
				},
				secret: {},
			},
			{ status: 201 },
		);
	}),

	// Credential providers list (the create dialog reads this).
	http.get('/credentials/providers', () =>
		HttpResponse.json({
			providers: [
				{
					id: 'static',
					label: 'Manual',
					managed: false,
					types: ['bearer_token', 'api_key', 'basic', 'oauth2', 'no_auth'],
					configured: true,
				},
			],
		}),
	),

	// Connect flow begin — in the showcase, treat any connect as immediately done.
	http.post('/credentials/:id/connect', () =>
		HttpResponse.json({ authorize_url: null, status: 'connected' }),
	),

	// Discard (orphan cleanup on cancel) — accept and no-op.
	http.delete('/toolkits/:id', () => new HttpResponse(null, { status: 204 })),
	http.delete('/credentials/:id', () => new HttpResponse(null, { status: 204 })),
];

/** Reset the mutable demo state (called when the showcase page mounts). */
export function resetDevShowcaseState(): void {
	for (const key of Object.keys(state)) delete state[key];
	Object.assign(state, structuredClone(SHOWCASE_BY_ID));
}

/**
 * Install the showcase handlers at runtime, scoped to the current MSW session.
 * `worker.use()` PREPENDS them (first-match-wins) so they claim
 * `/access-requests` (and the wizard's create/amend/decide) ahead of any
 * feature fixtures — and, because it's runtime not table-level, they never
 * leak into other pages or into unrelated component tests (which call
 * `worker.resetHandlers()` between runs). Resets demo state each time so a
 * re-visit starts from the fixtures.
 */
export function installDevShowcaseHandlers(): void {
	resetDevShowcaseState();
	worker.use(...devAccessRequestHandlers);
}
