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
 *  - a second GitHub credential, so adding GitHub to an agent asks which one;
 *  - a Library whose catalog and registry agree the way the backend derives
 *    them: the seeded Stripe / GitHub / Slack registry rows carry their
 *    `catalog_api_id`, those catalog entries read `registered`, and Stripe has
 *    an upstream update (`update_available` on both sides);
 *  - catalog imports that LAND: `POST /catalog/{id}:import` still answers 202,
 *    then a few seconds later the entry flips `registered` and a registry row
 *    appears (with `catalog_api_id`), as the real async import job does — so
 *    the Library panel's "Adding…" row resolves into the API list.
 */
import { http, HttpResponse, type HttpHandler } from 'msw';
import { CredentialType, type Credential } from '@/shared/credentials/api';
import { seedMockCredentials } from '@/shared/credentials/mocks/handlers';
import { mockCatalogVendor, patchMockCatalogEntry } from '@/modules/discover/mocks/handlers';
import { patchMockApi, registerMockCatalogImport } from '@/modules/workspace/mocks/handlers';

const STRIPE = { vendor: 'stripe', name: 'stripe-api' };
const GITHUB = { vendor: 'github', name: 'github-api' };

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

/** Seed the scenario's rows into the shared stores. Call once, before the worker starts. */
export function installReviewScenario(): void {
	seedMockCredentials(scenarioCredentials);
	// Line the catalog up with the registry (both fields are real wire fields).
	patchMockApi('stripe/stripe-api/2024-01-01', {
		catalog_api_id: 'stripe.com',
		origin: 'catalog',
		update_available: true,
	});
	patchMockApi('github/github-api/1.1.4', { catalog_api_id: 'github.com', origin: 'catalog' });
	patchMockApi('slack.com/web-api/1.0.0', { catalog_api_id: 'slack.com', origin: 'catalog' });
	patchMockCatalogEntry('stripe.com', { registered: true, update_available: true });
	patchMockCatalogEntry('github.com', { registered: true });
	patchMockCatalogEntry('slack.com', { registered: true });
}

/** How long a scenario catalog import takes to "land" (the async job). */
const IMPORT_LANDS_AFTER_MS = 5_000;

/** One pending "landing" per api_id, so a re-click doesn't stack timers. */
const landing = new Map<string, ReturnType<typeof setTimeout>>();

/**
 * Overrides the default (never-landing) catalog import: same 202 + job body,
 * then the job "completes" — the entry reads registered and the registry gains
 * the API — so the Library's pending → imported flow is reviewable end to end.
 */
export const reviewScenarioHandlers: HttpHandler[] = [
	http.post('/catalog/*', ({ request }) => {
		const url = new URL(request.url);
		const tail = decodeURIComponent(url.pathname.replace(/^\/catalog\//, ''));
		if (!tail.endsWith(':import')) return undefined;
		const apiId = tail.slice(0, -':import'.length);
		const jobId = `job_${apiId.replace(/\W/g, '_')}`;
		// Only in a live page (the dev worker): a timer outliving a non-browser
		// run would mutate the shared fixtures after it ended.
		if (typeof document !== 'undefined' && !landing.has(apiId)) {
			landing.set(
				apiId,
				setTimeout(() => {
					landing.delete(apiId);
					registerMockCatalogImport(apiId, mockCatalogVendor(apiId) ?? apiId);
					patchMockCatalogEntry(apiId, { registered: true });
				}, IMPORT_LANDS_AFTER_MS),
			);
		}
		return HttpResponse.json(
			{ job_id: jobId, status: 'queued', _links: { self: `/jobs/${jobId}` } },
			{ status: 202 },
		);
	}),
];
