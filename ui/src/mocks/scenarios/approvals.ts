/**
 * Opt-in mocked-dev scenario for reviewing Ask (`require-approval`) rules and
 * the held calls they produce, by hand:
 * `VITE_MSW_SCENARIO=approvals npm run dev:mock`.
 *
 * Replaces the default agent fleet and held-execution store with three active
 * agents, all owned by the signed-in admin:
 *  - `docs-indexer` — GitHub, allow rules only: nothing is ever held;
 *  - `billing-ops` — Stripe, four Ask rules, one pending held call per rule;
 *  - `release-manager` — GitHub + Slack, three Ask rules on GitHub, one pending
 *    held call per rule.
 *
 * Each held call's `matched_rule_id` names the Ask rule that held it, and its
 * request matches that rule. Three of them carry the agent's own reason for
 * the call (`agent_reason`), so the review pop-up shows the "Why the agent is
 * asking" box on those and no box on the rest. The two APIs' operation lists name each call the way
 * their real specs do ("Create a payout"), so every surface shows a headline
 * rather than a route. Never loaded by the Vitest setup — component tests and
 * e2e specs see the default fixtures only.
 */
import { http, HttpResponse } from 'msw';
import { seedMockActors } from '@/mocks/handlers';
import { seedMockAgentConnectSession } from '@/shared/credentials/mocks/handlers';
import {
	clearAgentsStore,
	seedCredentialBindings,
	seedExtraAgents,
} from '@/modules/agents/mocks/handlers';
import { clearApprovalsStore, seedMockApproval } from '@/modules/agents/mocks/approvalsHandlers';

/** The mock signed-in admin (resolves in the actor directory). */
const ADMIN = 'usr_000000000000000000000admin';
/** The signed-in viewer (`/users/me`): what they decided reads "you". */
const VIEWER = '00000000-0000-0000-0000-000000000001';
/** A second admin, who decided some of the history. */
const PRIYA = 'usr_00000000000000000000priya';

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
/** A pending hold's window: 24h from when it was held. */
const expiresFrom = (heldMinutesAgo: number) =>
	new Date(Date.now() + (24 * 60 - heldMinutesAgo) * 60_000).toISOString();

const AGENTS = [
	{
		id: 'agnt_docs_indexer',
		name: 'docs-indexer',
		description: 'Indexes the engineering handbook and READMEs for search.',
		created: 60 * 24 * 9,
	},
	{
		id: 'agnt_billing_ops',
		name: 'billing-ops',
		description: 'Reconciles invoices, issues refunds and chases failed payments.',
		created: 60 * 24 * 4,
	},
	{
		id: 'agnt_release_manager',
		name: 'release-manager',
		description: 'Merges approved PRs, cuts releases and posts release notes.',
		created: 60 * 24 * 2,
	},
] as const;

const STRIPE = { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' };
const GITHUB = { vendor: 'github', name: 'github-api', version: '1.1.4' };

/** One Ask rule plus the held call it produced. */
interface AskCase {
	ruleId: string;
	method: string;
	/** Regex the rule matches the upstream path with. */
	pattern: string;
	why: string;
	operationId: string;
	/** The operation's summary in the API's spec — the call's headline; absent when the spec names nothing. */
	summary?: string;
	/** The spec's path template, for an operation the spec leaves unnamed. */
	template?: string;
	/** The held call's concrete upstream path. */
	path: string;
	query?: string;
	body: Record<string, unknown> | null;
	heldMinutesAgo: number;
	/** The agent's own reason for the call, when it sent one. */
	reason?: string;
}

const BILLING_ASKS: AskCase[] = [
	{
		ruleId: 'apr_billing_charges',
		method: 'POST',
		pattern: '^/v1/charges$',
		why: 'Moves money: a person confirms every new charge.',
		operationId: 'PostCharges',
		summary: 'Create a charge',
		path: '/v1/charges',
		body: {
			amount: 129900,
			currency: 'eur',
			customer: 'cus_QK3n8xV1mZp2Lr',
			description: 'Annual plan renewal — Northwind Traders',
		},
		heldMinutesAgo: 6,
		reason: 'The annual plan for Northwind Traders renews today and its invoice is still open for the same amount, so I’m collecting it now rather than waiting for the retry schedule.',
	},
	{
		ruleId: 'apr_billing_refunds',
		method: 'POST',
		pattern: '^/v1/refunds$',
		why: 'Refunds are irreversible once paid out.',
		operationId: 'PostRefunds',
		summary: 'Create a refund',
		path: '/v1/refunds',
		body: { charge: 'ch_3PZx1kLm9QwErTy0', amount: 4900, reason: 'requested_by_customer' },
		heldMinutesAgo: 19,
	},
	{
		ruleId: 'apr_billing_cancel_sub',
		method: 'DELETE',
		pattern: '^/v1/subscriptions/[^/]+$',
		why: 'Cancelling a subscription stops revenue immediately.',
		operationId: 'DeleteSubscriptionsSubscriptionExposedId',
		summary: 'Cancel a subscription',
		path: '/v1/subscriptions/sub_1PbQ7tLm9QwErTy0aB3cD4eF', // pragma: allowlist secret
		query: 'invoice_now=true&prorate=true',
		body: null,
		heldMinutesAgo: 47,
	},
	{
		ruleId: 'apr_billing_payouts',
		method: 'POST',
		pattern: '^/v1/payouts$',
		why: 'Payouts leave the Stripe balance for the bank account.',
		operationId: 'PostPayouts',
		summary: 'Create a payout',
		path: '/v1/payouts',
		body: { amount: 2500000, currency: 'eur', method: 'standard' },
		heldMinutesAgo: 128,
		reason: 'Weekly treasury sweep: the available balance is above the float we keep in Stripe.',
	},
];

const RELEASE_ASKS: AskCase[] = [
	{
		ruleId: 'apr_release_merge',
		method: 'PUT',
		pattern: '^/repos/[^/]+/[^/]+/pulls/\\d+/merge$',
		why: 'Merging to main ships to production on the next deploy.',
		operationId: 'pulls/merge',
		summary: 'Merge a pull request',
		path: '/repos/acme-corp/storefront/pulls/1842/merge',
		body: { merge_method: 'squash', commit_title: 'Checkout: retry 3DS challenge (#1842)' },
		heldMinutesAgo: 3,
		reason: 'PR #1842 has two approving reviews and every check is green. It is the last item on the 4.12 release checklist.',
	},
	{
		ruleId: 'apr_release_publish',
		method: 'POST',
		pattern: '^/repos/[^/]+/[^/]+/releases$',
		why: 'A published release notifies every customer watching the repo.',
		operationId: 'repos/create-release',
		summary: 'Create a release',
		path: '/repos/acme-corp/storefront/releases',
		body: {
			tag_name: 'v4.12.0',
			target_commitish: 'main',
			name: 'Storefront 4.12.0',
			draft: false,
			prerelease: false,
		},
		heldMinutesAgo: 24,
	},
	{
		ruleId: 'apr_release_delete_branch',
		method: 'DELETE',
		pattern: '^/repos/[^/]+/[^/]+/git/refs/heads/.+$',
		why: 'Deleting a branch cannot be undone from the API.',
		operationId: 'git/delete-ref',
		// The spec names no summary here, so the headline is its path template.
		template: '/repos/{owner}/{repo}/git/refs/{ref}',
		path: '/repos/acme-corp/storefront/git/refs/heads/release/4.11',
		body: null,
		heldMinutesAgo: 71,
	},
];

function askRules(cases: AskCase[]) {
	return cases.map((c) => ({
		effect: 'require-approval' as const,
		match_mode: 'regex' as const,
		methods: [c.method],
		path: c.pattern,
		_comment: c.why,
	}));
}

function seedHolds(
	agent: (typeof AGENTS)[number],
	credentialId: string,
	host: string,
	api: { vendor: string; name: string; version: string },
	cases: AskCase[],
): void {
	for (const c of cases) {
		const id = `held_${c.ruleId.replace(/^apr_/, '')}`;
		seedMockApproval({
			id,
			job_id: `job_${id}`,
			agent_id: agent.id,
			agent_name: agent.name,
			agent_owner_id: ADMIN,
			credential_id: credentialId,
			api_vendor: api.vendor,
			api_name: api.name,
			api_version: api.version,
			operation_id: c.operationId,
			method: c.method,
			path: c.path,
			matched_rule_id: c.ruleId,
			trace_id: `trace_${id}`,
			created_at: minutesAgo(c.heldMinutesAgo),
			expires_at: expiresFrom(c.heldMinutesAgo),
			request: {
				method: c.method,
				url: `https://${host}${c.path}${c.query ? `?${c.query}` : ''}`,
				body: c.body ? JSON.stringify(c.body) : null,
				body_truncated: false,
			},
			agent_reason: c.reason ?? null,
		});
	}
}

/** One settled call in the Decided history, `askedMinutesAgo` after its case's ask. */
interface SettledCase {
	ask: AskCase;
	askedMinutesAgo: number;
	state: 'approved' | 'denied' | 'expired' | 'withdrawn';
	/** Where an approved call went. */
	ran?: 'completed' | 'failed';
	by?: string;
	reason?: string;
}

function seedSettled(
	agent: (typeof AGENTS)[number],
	credentialId: string,
	host: string,
	api: { vendor: string; name: string; version: string },
	cases: SettledCase[],
	idPrefix: string,
): void {
	cases.forEach((c, i) => {
		const id = `held_${idPrefix}_${i + 1}`;
		const asked = c.askedMinutesAgo;
		const decided = c.state === 'approved' || c.state === 'denied';
		seedMockApproval({
			id,
			job_id: `job_${id}`,
			agent_id: agent.id,
			agent_name: agent.name,
			agent_owner_id: ADMIN,
			credential_id: credentialId,
			api_vendor: api.vendor,
			api_name: api.name,
			api_version: api.version,
			operation_id: c.ask.operationId,
			method: c.ask.method,
			path: c.ask.path,
			matched_rule_id: c.ask.ruleId,
			state: c.state,
			execution_status: c.state === 'approved' ? (c.ran ?? 'completed') : 'failed',
			created_at: minutesAgo(asked),
			// A 24h window; an expired call ran out of it.
			expires_at: minutesAgo(asked - 24 * 60),
			decided_at: decided ? minutesAgo(asked - 9) : null,
			decided_by: decided ? (c.by ?? VIEWER) : null,
			decision_reason: c.reason ?? null,
			updated_at: c.state === 'withdrawn' ? minutesAgo(asked - 30) : null,
			request: {
				method: c.ask.method,
				url: `https://${host}${c.ask.path}`,
				body: c.ask.body ? JSON.stringify(c.ask.body) : null,
				body_truncated: false,
			},
			agent_reason: c.ask.reason ?? null,
		});
	});
}

/** The billing agent's history: one of each ending, then a run of routine approvals. */
function billingHistory(): SettledCase[] {
	const [charge, refund, , payout] = BILLING_ASKS;
	const named: SettledCase[] = [
		{ ask: charge, askedMinutesAgo: 180, state: 'approved' },
		{
			ask: refund,
			askedMinutesAgo: 300,
			state: 'denied',
			by: PRIYA,
			reason: 'Support is already refunding this in ticket #4821.',
		},
		{ ask: payout, askedMinutesAgo: 26 * 60, state: 'expired' },
	];
	// Older, routine approvals: enough history to page through.
	const routine = Array.from({ length: 24 }, (_, i): SettledCase => ({
		ask: i % 3 === 0 ? refund : charge,
		askedMinutesAgo: 30 * 60 + i * 4 * 60,
		state: 'approved',
		by: i % 4 === 0 ? PRIYA : VIEWER,
	}));
	return [...named, ...routine];
}

/** The release agent's history. */
function releaseHistory(): SettledCase[] {
	const [merge, release, deleteBranch] = RELEASE_ASKS;
	return [
		{ ask: merge, askedMinutesAgo: 22 * 60, state: 'approved', ran: 'failed', by: PRIYA },
		{ ask: release, askedMinutesAgo: 2 * 24 * 60, state: 'withdrawn' },
		{
			ask: deleteBranch,
			askedMinutesAgo: 3 * 24 * 60,
			state: 'denied',
			reason: 'Keep release/4.11 until the hotfix ships.',
		},
	];
}

/**
 * `GET /apis/{vendor}/{name}/{version}/operations` for the scenario's two APIs:
 * one row per held call's operation, named by its spec summary. Other APIs
 * fall through to the default fixtures.
 */
function operationsHandler(
	api: { vendor: string; name: string; version: string },
	cases: AskCase[],
) {
	return http.get(`/apis/${api.vendor}/${api.name}/${api.version}/operations`, () =>
		HttpResponse.json({
			data: cases.map((c) => ({
				_links: {
					inspect: `/apis/${api.vendor}/${api.name}/${api.version}/operations/${encodeURIComponent(c.operationId)}`,
				},
				api,
				method: c.method,
				name: c.summary ?? null,
				operation_id: c.operationId,
				path: c.template ?? c.path,
				revision_id: `rev_${api.name}`,
			})),
			has_more: false,
			next_cursor: null,
		}),
	);
}

/** Install with `worker.use(...)` alongside `installApprovalsScenario`. */
export const approvalsScenarioHandlers = [
	operationsHandler(STRIPE, BILLING_ASKS),
	operationsHandler(GITHUB, RELEASE_ASKS),
];

/** Seed the scenario into the shared stores. Call once, before the worker starts. */
export function installApprovalsScenario(): void {
	clearAgentsStore();
	clearApprovalsStore();
	seedExtraAgents(
		AGENTS.map((a) => ({
			id: a.id,
			name: a.name,
			description: a.description,
			status: 'active' as const,
			owner_id: ADMIN,
			registered_by: ADMIN,
			approved_by: ADMIN,
			created_at: minutesAgo(a.created),
			approved_at: minutesAgo(a.created - 5),
			has_api_key: true,
		})),
	);
	seedMockActors([
		...AGENTS.map((a) => ({
			id: a.id,
			actor_type: 'agent',
			name: a.name,
			active: true,
			created_at: minutesAgo(a.created),
		})),
		{
			id: PRIYA,
			actor_type: 'user',
			name: 'Priya Shah',
			active: true,
			created_at: minutesAgo(90 * 24 * 60),
		},
	]);

	const [docs, billing, release] = AGENTS;
	const servesStripe = [{ api_vendor: 'stripe', api_name: 'stripe-api', api_version: null }];
	const servesGithub = [{ api_vendor: 'github', api_name: null, api_version: null }];
	seedCredentialBindings([
		{
			agent_id: docs.id,
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			serves: servesGithub,
			permissions: [
				{ effect: 'allow', match_mode: 'prefix', methods: ['GET'], path: '/repos' },
				{ effect: 'allow', match_mode: 'prefix', methods: ['GET'], path: '/search' },
			],
		},
		{
			agent_id: billing.id,
			credential_id: 'cred_stripe_1',
			name: 'Stripe live key',
			serves: servesStripe,
			permissions: [
				...askRules(BILLING_ASKS),
				{ effect: 'allow', match_mode: 'prefix', methods: ['GET'], path: '/v1/' },
				{ effect: 'allow', match_mode: 'prefix', methods: ['POST'], path: '/v1/invoices' },
			],
		},
		{
			agent_id: release.id,
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			serves: servesGithub,
			permissions: [
				...askRules(RELEASE_ASKS),
				{ effect: 'allow', match_mode: 'prefix', methods: ['GET', 'POST'], path: '/repos' },
			],
		},
		{
			agent_id: release.id,
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			serves: [{ api_vendor: 'slack.com', api_name: null, api_version: null }],
			permissions: [
				{
					effect: 'allow',
					match_mode: 'exact',
					methods: ['POST'],
					path: '/chat.postMessage',
				},
			],
		},
	]);

	seedHolds(billing, 'cred_stripe_1', 'api.stripe.com', STRIPE, BILLING_ASKS);
	seedHolds(release, 'cred_github_1', 'api.github.com', GITHUB, RELEASE_ASKS);
	seedSettled(billing, 'cred_stripe_1', 'api.stripe.com', STRIPE, billingHistory(), 'billing');
	seedSettled(release, 'cred_github_1', 'api.github.com', GITHUB, releaseHistory(), 'release');

	// release-manager also asked to connect an account: the "Waiting for you"
	// row beside the held calls.
	seedMockAgentConnectSession({ agent_id: release.id, vendor_key: 'slack.com' });
}
