/**
 * Access-request possibility fixtures — the full space of shapes the system can
 * produce, for the dev-only showcase at `/app/dev/access-requests`. Each entry
 * is a real `AccessRequest` (the shared lib type) plus a note on how the UI
 * routes and handles it, so the gallery can render every case against MSW
 * without a live backend.
 *
 * DEV-ONLY: imported only by the dev showcase route, which is guarded by
 * `import.meta.env.DEV` and tree-shaken out of production builds.
 */
import type { AccessRequest, AccessRequestItem } from '@/shared/lib';

const AGENT = 'agnt_showcase_0001';
const OWNER = 'usr_owner_0001';

function item(
	p: Partial<AccessRequestItem> & { resource_type: string; action: string },
): AccessRequestItem {
	return {
		id: p.id ?? `arqi_${p.resource_type}_${p.action}`,
		resource_type: p.resource_type,
		action: p.action,
		status: p.status ?? 'pending',
		resource_id: p.resource_id ?? null,
		resource_reference: p.resource_reference ?? null,
		to_type: p.to_type ?? null,
		to_id: p.to_id ?? null,
		rules: p.rules ?? null,
		decided_by: p.decided_by ?? null,
		decided_at: p.decided_at ?? null,
		decision_reason: p.decision_reason ?? null,
	};
}

function req(
	id: string,
	status: string,
	items: AccessRequestItem[],
	reason?: string,
): AccessRequest {
	return {
		id,
		actor_id: AGENT,
		status,
		reason: reason ?? null,
		requested_by: AGENT,
		filed_at: '2026-07-23T09:00:00Z',
		expires_at: '2026-07-30T09:00:00Z',
		items,
	};
}

const API = { vendor: 'posthog-com', name: 'posthog-api', version: '1.0.0' };
const RULES = [{ effect: 'allow', methods: ['GET'], path: '.*' }];

/** How the UI routes/handles a given request — surfaced as a note in the gallery. */
export type RoutedTo = 'wizard' | 'plain' | 'rail-handoff';

export interface ShowcaseCase {
	key: string;
	title: string;
	summary: string;
	/** Which decision surface this opens (see AccessRequestDecisionDialog). */
	routedTo: RoutedTo;
	request: AccessRequest;
}

export const SHOWCASE_CASES: ShowcaseCase[] = [
	// ── Provisioning plans (open the wizard) ──────────────────────────────────
	{
		key: 'plan-oauth-pending',
		title: 'Provisioning plan · OAuth2 · pending',
		summary:
			'The full path to first execution: create toolkit, provision an OAuth2 credential, bind it with proposed rules, bind the agent. Opens the setup wizard.',
		routedTo: 'wizard',
		request: req(
			'areq_plan_oauth',
			'pending',
			[
				item({ resource_type: 'toolkit', action: 'create', resource_reference: API }),
				item({
					resource_type: 'credential',
					action: 'provision',
					resource_reference: { ...API, security_scheme: 'oauth2' },
				}),
				item({ resource_type: 'credential', action: 'bind', rules: RULES }),
				item({ resource_type: 'toolkit', action: 'bind', resource_reference: API }),
			],
			"Fetch the user's PostHog dashboards",
		),
	},
	{
		key: 'plan-noauth-pending',
		title: 'Provisioning plan · no-auth · pending',
		summary:
			'A no-auth API (e.g. open-meteo): the provision item carries security_scheme=no_auth, so the wizard skips the manual credential step and auto-creates a NO_AUTH credential on approval.',
		routedTo: 'wizard',
		request: req('areq_plan_noauth', 'pending', [
			item({
				resource_type: 'toolkit',
				action: 'create',
				resource_reference: {
					vendor: 'open-meteo-com',
					name: 'forecast',
					version: '1.0.0',
				},
			}),
			item({
				resource_type: 'credential',
				action: 'provision',
				resource_reference: {
					vendor: 'open-meteo-com',
					name: 'forecast',
					version: '1.0.0',
					security_scheme: 'no_auth',
				},
			}),
			item({ resource_type: 'credential', action: 'bind', rules: RULES }),
			item({
				resource_type: 'toolkit',
				action: 'bind',
				resource_reference: {
					vendor: 'open-meteo-com',
					name: 'forecast',
					version: '1.0.0',
				},
			}),
		]),
	},
	{
		key: 'plan-partially-denied',
		title: 'Provisioning plan · plain-approved (guard denied the binds)',
		summary:
			'A plan approved WITHOUT the wizard: the inert intents approve as no-ops but the binds are denied with a plan-aware reason pointing back to the wizard.',
		routedTo: 'wizard',
		request: req('areq_plan_denied', 'partially_approved', [
			item({
				resource_type: 'toolkit',
				action: 'create',
				status: 'approved',
				resource_reference: API,
				decided_by: OWNER,
			}),
			item({
				resource_type: 'credential',
				action: 'provision',
				status: 'approved',
				resource_reference: { ...API, security_scheme: 'bearer' },
				decided_by: OWNER,
			}),
			item({
				resource_type: 'credential',
				action: 'bind',
				status: 'denied',
				rules: RULES,
				decided_by: OWNER,
				decision_reason:
					'credential:bind is part of a provisioning plan that has not been fulfilled yet. Approve this request from the setup wizard.',
			}),
			item({
				resource_type: 'toolkit',
				action: 'bind',
				status: 'denied',
				resource_reference: API,
				decided_by: OWNER,
				decision_reason:
					'toolkit:bind is part of a provisioning plan that has not been fulfilled yet. Approve this request from the setup wizard.',
			}),
		]),
	},
	{
		key: 'plan-approved',
		title: 'Provisioning plan · approved (fulfilled)',
		summary:
			'A plan fulfilled through the wizard and approved: the read-only summary reconstructs what was wired — toolkit, credential, the rules the agent got, and when.',
		routedTo: 'wizard',
		request: req('areq_plan_approved', 'approved', [
			item({
				resource_type: 'toolkit',
				action: 'create',
				status: 'approved',
				resource_reference: API,
				decided_by: OWNER,
				decided_at: '2026-07-23T09:05:00Z',
			}),
			item({
				resource_type: 'credential',
				action: 'provision',
				status: 'approved',
				resource_reference: { ...API, security_scheme: 'oauth2' },
				decided_by: OWNER,
				decided_at: '2026-07-23T09:05:00Z',
			}),
			item({
				resource_type: 'credential',
				action: 'bind',
				status: 'approved',
				resource_id: 'cred_posthog_9f2',
				to_type: 'toolkit',
				to_id: 'tk_posthog_a17',
				rules: RULES,
				decided_by: OWNER,
				decided_at: '2026-07-23T09:05:00Z',
			}),
			item({
				resource_type: 'toolkit',
				action: 'bind',
				status: 'approved',
				resource_id: 'tk_posthog_a17',
				resource_reference: API,
				decided_by: OWNER,
				decided_at: '2026-07-23T09:05:00Z',
			}),
		]),
	},

	// ── Single-item requests (open the plain approve/deny dialog) ─────────────
	{
		key: 'toolkit-bind-pending',
		title: 'toolkit:bind · pending',
		summary:
			'Last-mile: bind the agent to an EXISTING toolkit that already serves the API. Plain approve/deny.',
		routedTo: 'plain',
		request: req('areq_tk_bind', 'pending', [
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: API }),
		]),
	},
	{
		key: 'scope-grant-pending',
		title: 'scope:grant · pending',
		summary: 'Grant the agent a platform scope (e.g. apis:write). Plain approve/deny.',
		routedTo: 'plain',
		request: req('areq_scope', 'pending', [
			item({ resource_type: 'scope', action: 'grant', resource_id: 'apis:write' }),
		]),
	},
	{
		key: 'credential-bind-pending',
		title: 'credential:bind · pending (with rules)',
		summary:
			'Bind an existing credential to a toolkit with permission rules. Plain approve/deny; rules are enforceable here.',
		routedTo: 'plain',
		request: req('areq_cred_bind', 'pending', [
			item({
				resource_type: 'credential',
				action: 'bind',
				resource_id: 'cred_abc123',
				to_type: 'toolkit',
				to_id: 'tk_xyz789',
				rules: RULES,
			}),
		]),
	},

	// ── Terminal states (read-only) ───────────────────────────────────────────
	{
		key: 'approved',
		title: 'scope:grant · approved',
		summary: 'A decided request in its terminal approved state (read-only).',
		routedTo: 'plain',
		request: req('areq_approved', 'approved', [
			item({
				resource_type: 'scope',
				action: 'grant',
				resource_id: 'apis:read',
				status: 'approved',
				decided_by: OWNER,
			}),
		]),
	},
	{
		key: 'denied',
		title: 'toolkit:bind · denied',
		summary: 'A denied request with a reason the agent reads back.',
		routedTo: 'plain',
		request: req('areq_denied', 'denied', [
			item({
				resource_type: 'toolkit',
				action: 'bind',
				resource_reference: API,
				status: 'denied',
				decided_by: OWNER,
				decision_reason:
					'No toolkit serves API posthog-com/posthog-api; provision and bind a credential first.',
			}),
		]),
	},
	{
		key: 'withdrawn',
		title: 'scope:grant · withdrawn',
		summary: 'The agent withdrew the request before a decision.',
		routedTo: 'plain',
		request: req('areq_withdrawn', 'withdrawn', [
			item({
				resource_type: 'scope',
				action: 'grant',
				resource_id: 'apis:write',
				status: 'withdrawn',
			}),
		]),
	},
	{
		key: 'expired',
		title: 'toolkit:bind · expired',
		summary: 'The request TTL elapsed with no decision.',
		routedTo: 'plain',
		request: req('areq_expired', 'expired', [
			item({
				resource_type: 'toolkit',
				action: 'bind',
				resource_reference: API,
				status: 'pending',
			}),
		]),
	},
];

/** Index by request id — the MSW handlers resolve GET/decide/amend against this. */
export const SHOWCASE_BY_ID: Record<string, AccessRequest> = Object.fromEntries(
	SHOWCASE_CASES.map((c) => [c.request.id, c.request]),
);
