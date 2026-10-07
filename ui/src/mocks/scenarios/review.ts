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
 *    the catalog row's "Adding…" resolves and the API joins the panel's list.
 */
import { http, HttpResponse, type HttpHandler } from 'msw';
import { CredentialType, type Credential } from '@/shared/credentials/api';
import { seedMockCredentials } from '@/shared/credentials/mocks/handlers';
import {
	addMockCatalogEntries,
	mockCatalogVendor,
	patchMockCatalogEntry,
	type MockCatalogRow,
} from '@/modules/discover/mocks/handlers';
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

/**
 * A realistic slice of the public catalog for reviewing the Library ledger:
 * a big umbrella vendor (googleapis.com, > 5 sub-APIs → one collapsed row),
 * small multi-API vendors (2–5 → header + children), and single-API vendors
 * across the alphabet — enough rows to span more than one keyset page.
 */
const SCENARIO_CATALOG: MockCatalogRow[] = [
	{ api_id: 'googleapis.com/gmail', vendor: 'googleapis.com', version: 'v1' },
	{ api_id: 'googleapis.com/drive', vendor: 'googleapis.com', version: 'v3' },
	{ api_id: 'googleapis.com/calendar', vendor: 'googleapis.com', version: 'v3' },
	{ api_id: 'googleapis.com/sheets', vendor: 'googleapis.com', version: 'v4' },
	{ api_id: 'googleapis.com/youtube', vendor: 'googleapis.com', version: 'v3' },
	{ api_id: 'googleapis.com/translate', vendor: 'googleapis.com', version: 'v3' },
	{ api_id: 'googleapis.com/storage', vendor: 'googleapis.com', version: 'v1' },
	{ api_id: 'googleapis.com/bigquery', vendor: 'googleapis.com', version: 'v2' },
	{ api_id: 'googleapis.com/people', vendor: 'googleapis.com', version: 'v1' },
	{ api_id: '0xerr0r.github.io', vendor: '0xerr0r.github.io', version: '0.1.0' },
	{ api_id: '100hires.com', vendor: '100hires.com', version: '1.0.0' },
	{ api_id: '100ms.live', vendor: '100ms.live', version: '2.0.0' },
	{ api_id: '123formbuilder.com', vendor: '123formbuilder.com', version: '2.0.0' },
	{ api_id: '15five.com', vendor: '15five.com', version: '1.0.0' },
	{ api_id: '1forge.com', vendor: '1forge.com', version: '0.0.1' },
	{ api_id: 'abstractapi.com', vendor: 'abstractapi.com', version: '1.0.0' },
	{ api_id: 'adyen.com', vendor: 'adyen.com', version: '71' },
	{ api_id: 'airtable.com', vendor: 'airtable.com', version: '0.1.0' },
	{ api_id: 'algolia.com', vendor: 'algolia.com', version: '1.0.0' },
	{ api_id: 'asana.com', vendor: 'asana.com', version: '1.0.0' },
	{ api_id: 'auth0.com', vendor: 'auth0.com', version: '2.0.0' },
	{ api_id: 'bitbucket.org', vendor: 'bitbucket.org', version: '2.0' },
	{ api_id: 'box.com', vendor: 'box.com', version: '2.0.0' },
	{ api_id: 'brex.com', vendor: 'brex.com', version: '1.0.0' },
	{ api_id: 'calendly.com', vendor: 'calendly.com', version: '2.0.0' },
	{ api_id: 'circleci.com', vendor: 'circleci.com', version: '2.0' },
	{ api_id: 'clickup.com', vendor: 'clickup.com', version: '2.0' },
	{ api_id: 'cloudflare.com', vendor: 'cloudflare.com', version: '4.0.0' },
	{ api_id: 'datadoghq.com', vendor: 'datadoghq.com', version: '1.0' },
	{ api_id: 'digitalocean.com', vendor: 'digitalocean.com', version: '2.0' },
	{ api_id: 'discord.com', vendor: 'discord.com', version: '10' },
	{ api_id: 'docusign.net', vendor: 'docusign.net', version: '2.1' },
	{ api_id: 'dropbox.com', vendor: 'dropbox.com', version: '2.0' },
	{ api_id: 'elastic.co', vendor: 'elastic.co', version: '8.0' },
	{ api_id: 'etsy.com', vendor: 'etsy.com', version: '3.0.0' },
	{ api_id: 'figma.com', vendor: 'figma.com', version: '1.0.0' },
	{ api_id: 'freshdesk.com', vendor: 'freshdesk.com', version: '2.0' },
	{ api_id: 'hubspot.com', vendor: 'hubspot.com', version: '3.0' },
	{ api_id: 'intercom.io', vendor: 'intercom.io', version: '2.10' },
	{ api_id: 'jira.atlassian.com', vendor: 'jira.atlassian.com', version: '3.0' },
	{ api_id: 'klaviyo.com', vendor: 'klaviyo.com', version: '2024-02-15' },
	{ api_id: 'linear.app', vendor: 'linear.app', version: '1.0.0' },
	{ api_id: 'mailchimp.com', vendor: 'mailchimp.com', version: '3.0.0' },
	{ api_id: 'miro.com', vendor: 'miro.com', version: '2.0' },
	{ api_id: 'notion.com', vendor: 'notion.com', version: '2022-06-28' },
	{ api_id: 'okta.com', vendor: 'okta.com', version: '1.0.0' },
	{ api_id: 'openai.com', vendor: 'openai.com', version: '2.0.0' },
	{ api_id: 'pagerduty.com', vendor: 'pagerduty.com', version: '2.0' },
	{ api_id: 'paypal.com', vendor: 'paypal.com', version: '2.0' },
	{ api_id: 'plaid.com', vendor: 'plaid.com', version: '2020-09-14' },
	{ api_id: 'postmarkapp.com', vendor: 'postmarkapp.com', version: '1.0.0' },
	{ api_id: 'quickbooks.com', vendor: 'quickbooks.com', version: '3.0' },
	{ api_id: 'resend.com', vendor: 'resend.com', version: '1.0.0' },
	{ api_id: 'salesforce.com', vendor: 'salesforce.com', version: '58.0' },
	{ api_id: 'sendgrid.com', vendor: 'sendgrid.com', version: '3.0' },
	{ api_id: 'shopify.com', vendor: 'shopify.com', version: '2024-01' },
	{ api_id: 'square.com', vendor: 'square.com', version: '2.0' },
	{ api_id: 'trello.com', vendor: 'trello.com', version: '1.0' },
	{ api_id: 'typeform.com', vendor: 'typeform.com', version: '1.0' },
	{ api_id: 'vercel.com', vendor: 'vercel.com', version: '1.0' },
	{ api_id: 'webflow.com', vendor: 'webflow.com', version: '2.0.0' },
	{ api_id: 'xero.com', vendor: 'xero.com', version: '2.0' },
	{ api_id: 'youtrack.com', vendor: 'youtrack.com', version: '1.0' },
	{ api_id: 'zendesk.com', vendor: 'zendesk.com', version: '2.0' },
	{ api_id: 'zoom.us', vendor: 'zoom.us', version: '2.0.0' },
	{ api_id: '1password.com/events', vendor: '1password.com', version: '1.0.0' },
	{ api_id: '1password.com/connect', vendor: '1password.com', version: '1.0.0' },
	{ api_id: 'amazonaws.com/ec2', vendor: 'amazonaws.com', version: '2016-11-15' },
	{ api_id: 'amazonaws.com/s3', vendor: 'amazonaws.com', version: '2016-11-15' },
	{ api_id: 'amazonaws.com/lambda', vendor: 'amazonaws.com', version: '2016-11-15' },
	{ api_id: 'amazonaws.com/dynamodb', vendor: 'amazonaws.com', version: '2016-11-15' },
	{ api_id: 'amazonaws.com/sqs', vendor: 'amazonaws.com', version: '2016-11-15' },
	{ api_id: 'twilio.com/messaging', vendor: 'twilio.com', version: '1.55.0' },
	{ api_id: 'twilio.com/verify', vendor: 'twilio.com', version: '1.55.0' },
	{ api_id: 'twilio.com/voice', vendor: 'twilio.com', version: '1.55.0' },
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
	addMockCatalogEntries(SCENARIO_CATALOG);
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
