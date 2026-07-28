/**
 * Access-request shape catalog — the full space of request shapes the system
 * can produce, used to test that every shape routes to (and renders in) the
 * right decision surface. Each entry pairs a real `AccessRequest` with the
 * surface it should open, so `accessRequestShapes.test.tsx` can assert routing
 * exhaustively.
 *
 * Kept as a shared fixture (not a shipped feature): it documents the shapes as
 * data and backs the routing tests. If you add a new access-request shape,
 * add it here so the routing assertions cover it.
 */
import type { AccessRequest, AccessRequestItem } from '@/shared/lib';

const AGENT = 'agnt_shape_0001';
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

/** Which decision surface a request should open (see AccessRequestDecisionDialog). */
export type RoutedTo = 'wizard' | 'plain';

export interface AccessRequestShape {
	key: string;
	title: string;
	routedTo: RoutedTo;
	request: AccessRequest;
}

export const ACCESS_REQUEST_SHAPES: AccessRequestShape[] = [
	// ── Provisioning plans (open the wizard) ──────────────────────────────────
	{
		key: 'plan-oauth-pending',
		title: 'Provisioning plan · OAuth2 · pending',
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
		routedTo: 'plain',
		request: req('areq_tk_bind', 'pending', [
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: API }),
		]),
	},
	{
		key: 'scope-grant-pending',
		title: 'scope:grant · pending',
		routedTo: 'plain',
		request: req('areq_scope', 'pending', [
			item({ resource_type: 'scope', action: 'grant', resource_id: 'apis:write' }),
		]),
	},
	{
		key: 'credential-bind-pending',
		title: 'credential:bind · pending (with rules)',
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
