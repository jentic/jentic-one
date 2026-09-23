/**
 * Opt-in mocked-dev scenario for reviewing the agent ↔ credential flows by hand:
 * `VITE_ENABLE_MSW=1 VITE_MSW_SCENARIO=review npm run dev`.
 *
 * Layers on top of the default handler table (via `worker.use`, so it wins) and
 * is never loaded by the Vitest setup — component tests and e2e specs see the
 * default fixtures only.
 *
 * What it adds:
 *  - several Stripe credentials: two for the same API (one sharing a name with
 *    the seeded key) and one for a different Stripe API, so grouping, the
 *    "which credential" choice and the wizard's vendor-wide list are on screen;
 *  - a second GitHub credential, so a request to use GitHub asks which one;
 *  - two requests filed by `support-agent` (`agnt_active_1`): a provisioning
 *    plan the wizard fulfils (provision + bind), and a plain request to use
 *    GitHub, decided in the approve/deny dialog;
 *  - rail events for both, and `:amend` / `:decide` verbs over the scenario rows.
 */
import { http, HttpResponse } from 'msw';
import { CredentialType, type Credential } from '@/shared/credentials/api';
import type { AccessRequest, ItemAmendment, ItemDecision } from '@/shared/lib/accessRequests';
import { seedMockCredentials } from '@/shared/credentials/mocks/handlers';
import { seedRailEvents } from '@/shared/app/rail/mocks/handlers';
import { seedCredentialBindings } from '@/modules/agents/mocks/handlers';
import { dashboardPendingAccessRequests } from '@/modules/dashboard/mocks/handlers';

const AGENT_ID = 'agnt_active_1';
const STRIPE = { vendor: 'stripe', name: 'stripe-api' };
const GITHUB = { vendor: 'github', name: 'github-api' };

const ago = (min: number): string => new Date(Date.now() - min * 60_000).toISOString();

const staticCredential = {
	provider: 'static',
	active: true,
	provider_account_ref: null,
	updated_at: null,
} as const;

const scenarioCredentials: Credential[] = [
	{
		...staticCredential,
		credential_id: 'cred_stripe_sandbox',
		name: 'Stripe sandbox key',
		type: CredentialType.API_KEY,
		api: { ...STRIPE, version: '2024-01-01' },
		catalog_api_id: 'stripe.com',
		details: { location: 'header', field_name: 'X-Api-Key', hint: '••••test' },
		created_at: '2026-06-12T09:30:00Z',
	},
	{
		// Same name as the seeded `cred_stripe_1` — only the date and id tail tell
		// the two apart.
		...staticCredential,
		credential_id: 'cred_stripe_live_eu',
		name: 'Stripe live key',
		type: CredentialType.API_KEY,
		api: { ...STRIPE, version: '2024-01-01' },
		catalog_api_id: 'stripe.com',
		details: { location: 'header', field_name: 'X-Api-Key', hint: '••••eu91' },
		created_at: '2026-08-04T15:10:00Z',
	},
	{
		// Same vendor, different API: must not be offered for `stripe-api`.
		...staticCredential,
		credential_id: 'cred_stripe_connect',
		name: 'Stripe Connect platform key',
		type: CredentialType.BEARER_TOKEN,
		api: { vendor: 'stripe', name: 'connect', version: '2024-01-01' },
		details: { hint: '••••cnct' },
		created_at: '2026-07-20T11:00:00Z',
	},
	{
		// A second credential for the seeded `cred_github_1`'s API.
		...staticCredential,
		credential_id: 'cred_github_bot',
		name: 'GitHub bot account',
		type: CredentialType.BEARER_TOKEN,
		api: { ...GITHUB, version: '1.1.4' },
		catalog_api_id: 'github.com',
		details: { hint: '••••b0t2' },
		created_at: '2026-08-29T10:15:00Z',
	},
];

function scenarioRequests(): AccessRequest[] {
	const base = {
		actor_id: AGENT_ID,
		status: 'pending',
		requested_by: AGENT_ID,
		created_by: AGENT_ID,
		filer_owner_id: null,
		expires_at: ago(-24 * 60),
		evaluation: { can_fulfill: true, checks: [] },
	};
	return [
		{
			...base,
			id: 'arq_review_stripe',
			reason: 'Needs to look up charges and refunds for support tickets.',
			approve_url: 'https://app.example.test/access-requests/arq_review_stripe',
			filed_at: ago(4),
			items: [
				{
					id: 'ari_review_stripe_provision',
					resource_type: 'credential',
					action: 'provision',
					status: 'pending',
					resource_reference: { ...STRIPE, security_scheme: 'api_key' },
				},
				{
					id: 'ari_review_stripe_bind',
					resource_type: 'credential',
					action: 'bind',
					status: 'pending',
					resource_reference: STRIPE,
					rules: [{ effect: 'allow', methods: ['GET'] }],
				},
			],
		},
		{
			...base,
			id: 'arq_review_github',
			reason: 'Wants to open pull requests for the bugs it triages.',
			approve_url: 'https://app.example.test/access-requests/arq_review_github',
			filed_at: ago(9),
			items: [
				{
					id: 'ari_review_github_bind',
					resource_type: 'credential',
					action: 'bind',
					status: 'pending',
					resource_reference: GITHUB,
					rules: [
						{
							effect: 'allow',
							methods: ['GET', 'POST'],
							operations: ['pulls/create', 'pulls/list', 'repos/get'],
						},
						{ effect: 'deny', methods: ['DELETE'] },
					],
				},
			],
		},
	];
}

let requests: AccessRequest[] = [];

/** The API each scenario request's rail event names. */
const EVENT_API: Record<string, string> = {
	arq_review_stripe: 'Stripe',
	arq_review_github: 'GitHub',
};

/** Seed the scenario's rows into the shared stores. Call once, before the worker starts. */
export function installReviewScenario(): void {
	requests = scenarioRequests();
	seedMockCredentials(scenarioCredentials);
	seedRailEvents(
		requests.map((r) => ({
			event_id: `evt_review_${r.id}`,
			type: 'access_request.filed',
			severity: 'info' as const,
			summary: `Access request filed: ${EVENT_API[r.id] ?? 'API access'}`,
			detail: r.reason ?? null,
			requires_action: true,
			created_at: r.filed_at,
			data: { request_id: r.id, agent_id: AGENT_ID },
		})),
	);
}

function findRequest(path: string, verb: string): AccessRequest | undefined {
	const match = path.match(new RegExp(`/access-requests/([^/]+):${verb}$`));
	const id = match ? decodeURIComponent(match[1]) : '';
	return requests.find((r) => r.id === id);
}

type BindingRow = Parameters<typeof seedCredentialBindings>[0][number];

/** Wire the agent's binding for every approved bind that names a credential. */
function applyApprovedBinds(ar: AccessRequest): void {
	for (const item of ar.items) {
		if (item.action !== 'bind' || item.status !== 'approved' || !item.resource_id) continue;
		const ref = (item.resource_reference ?? {}) as { vendor?: string; name?: string };
		seedCredentialBindings([
			{
				agent_id: ar.actor_id,
				credential_id: item.resource_id,
				serves: ref.vendor ? [{ api_vendor: ref.vendor, api_name: ref.name ?? null }] : [],
				permissions: (item.rules ?? []) as BindingRow['permissions'],
			},
		]);
	}
}

export const reviewScenarioHandlers = [
	http.get('/access-requests', ({ request }) => {
		const url = new URL(request.url);
		const status = url.searchParams.get('status');
		const actorId = url.searchParams.get('actor_id');
		const ours = requests.filter((r) => !status || r.status === status);
		// The org-wide pending queue: the dashboard fixtures plus ours.
		if (status === 'pending' && !actorId) {
			return HttpResponse.json({
				data: [...ours, ...dashboardPendingAccessRequests],
				has_more: false,
				next_cursor: null,
			});
		}
		if (actorId === AGENT_ID) {
			return HttpResponse.json({ data: ours, has_more: false, next_cursor: null });
		}
		return undefined;
	}),

	http.get('/access-requests/:id', ({ params }) => {
		const ar = requests.find((r) => r.id === String(params.id));
		return ar ? HttpResponse.json(ar) : undefined;
	}),

	http.post(/\/access-requests\/[^/]+:amend$/, async ({ request }) => {
		const ar = findRequest(new URL(request.url).pathname, 'amend');
		if (!ar) return undefined;
		const body = (await request.json().catch(() => ({}))) as { items?: ItemAmendment[] };
		for (const amendment of body.items ?? []) {
			const item = ar.items.find((i) => i.id === amendment.item_id);
			if (!item) continue;
			if (amendment.resource_id !== undefined) item.resource_id = amendment.resource_id;
			if (amendment.rules !== undefined)
				item.rules = amendment.rules as Record<string, unknown>[] | null;
			if (amendment.rule_set_id !== undefined) item.rule_set_id = amendment.rule_set_id;
		}
		return HttpResponse.json(ar);
	}),

	http.post(/\/access-requests\/[^/]+:decide$/, async ({ request }) => {
		const ar = findRequest(new URL(request.url).pathname, 'decide');
		if (!ar) return undefined;
		const body = (await request.json().catch(() => ({}))) as { items?: ItemDecision[] };
		for (const decision of body.items ?? []) {
			const item = ar.items.find((i) => i.id === decision.item_id);
			if (!item) continue;
			item.status = decision.decision;
			item.decision_reason = decision.decision_reason ?? null;
			item.decided_at = new Date().toISOString();
		}
		const allDenied = ar.items.every((i) => i.status === 'denied');
		const allApproved = ar.items.every((i) => i.status === 'approved');
		ar.status = allDenied ? 'denied' : allApproved ? 'approved' : 'partially_approved';
		applyApprovedBinds(ar);
		return HttpResponse.json(ar);
	}),
];
