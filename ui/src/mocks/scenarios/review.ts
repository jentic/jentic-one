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
 *  - a second GitHub credential, so adding GitHub to an agent asks which one.
 */
import type { HttpHandler } from 'msw';
import { CredentialType, type Credential } from '@/shared/credentials/api';
import { seedMockCredentials } from '@/shared/credentials/mocks/handlers';

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
}

/** The scenario only seeds shared stores; it serves no routes of its own. */
export const reviewScenarioHandlers: HttpHandler[] = [];
