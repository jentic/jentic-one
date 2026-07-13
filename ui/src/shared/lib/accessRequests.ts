/**
 * Agent Rail — repository tier for access-request DECISIONS.
 *
 * This is the real "feed the agent back" mechanism. An agent files an access
 * request (`POST /access-requests`); the backend emits an `access_request.filed`
 * event (severity info, `requires_action: true`) carrying `data.request_id`.
 * That event is what the rail surfaces. The decision itself is a SEPARATE,
 * authoritative action on the access-request resource:
 *
 *   GET  /access-requests/{id}           — fetch items (we need their ids)
 *   POST /access-requests/{id}:decide    — approve/deny each item (+ reason)
 *
 * Acknowledging the *event* (`PATCH /events/{id}`, see `railEvents.ts`) only
 * dismisses the notification; it does not decide the request. Deny carries a
 * `decision_reason`, which is how the human's "no, because…" reaches the agent
 * (the agent reads it back off the request's `decision_reason` / status).
 *
 * Why hand-rolled rather than a generated `AccessRequestsService`: the endpoints
 * exist in `openapi.json` (tag "Access Requests", `operationId: decideAccessRequest`)
 * but the committed generated client predates a codegen grouping change, so it has
 * no such service. Regenerating restructures the entire client and is out of scope
 * here, so — exactly like the rail's SSE client — these calls go through the same
 * low-level `request(OpenAPI, …)` primitive the generated services use, imported
 * from the `@/shared/api` facade so the Bearer-JWT config is always applied.
 */
import { OpenAPI, apiRequest } from '@/shared/api';
import { toRailError } from '@/shared/lib/railEvents';

/**
 * A single line item on an access request.
 *
 * The backend (`AccessRequestItemResponse`) returns a fully specified target
 * (`resource_id` | `resource_reference`, plus an optional `to_*` assignment and
 * permission `rules`). The decision surface uses these to label the item and to
 * tell a SPECIFIC resource grant (a concrete `resource_id`) apart from a broad
 * ACTION/assignment — the webapp's blue "specific" vs orange "assignment" accents.
 * All fields beyond `id`/`resource_type`/`action`/`status` are optional so lean
 * callers (the rail badge, the dashboard count) can ignore them.
 */
export interface AccessRequestItem {
	id: string;
	resource_type: string;
	action: string;
	status: string;
	/** A concrete target id (e.g. a toolkit/credential id); absent for broad grants. */
	resource_id?: string | null;
	/** A structured target reference (e.g. `{vendor,name,version}`) when there's no id. */
	resource_reference?: Record<string, unknown> | null;
	/** Assignment target type/id (e.g. assign a toolkit TO an agent) — the "deviation" context. */
	to_type?: string | null;
	to_id?: string | null;
	/** Permission rules attached to the item (effect/methods/path/operations). */
	rules?: Record<string, unknown>[] | null;
	decided_by?: string | null;
	decided_at?: string | null;
	decision_reason?: string | null;
}

/** Permission-rule effect — `require-approval` exists in the schema but is rarely shown. */
export type PermissionRuleEffect = 'allow' | 'deny' | 'require-approval';

/**
 * A single permission rule on a `credential.bind` item. On approval these are
 * written verbatim as the binding's `ToolkitPermissionRule`s (broker-enforced,
 * ordered, first-match-wins, default-deny), so the reviewer is effectively
 * granting exactly this allow/block list of operations.
 */
export interface PermissionRule {
	effect: PermissionRuleEffect;
	/** HTTP methods the rule matches (e.g. `["GET","POST"]`); null = any. */
	methods?: string[] | null;
	/** Path/regex the rule matches; null = any. */
	path?: string | null;
	/** OpenAPI operationIds the rule matches; null = any. */
	operations?: string[] | null;
}

/** A single evaluation check (why the caller can / cannot fulfill the request). */
export interface AccessRequestEvaluationCheck {
	check: string;
	passed: boolean;
	blocker?: string | null;
}

/** Computed gate telling whether the CURRENT caller may decide this request. */
export interface AccessRequestEvaluation {
	can_fulfill: boolean;
	checks: AccessRequestEvaluationCheck[];
}

/** Access-request envelope (the fields the rail, dashboard card + decision surface need). */
export interface AccessRequest {
	id: string;
	/** The agent the request acts on behalf of — the real "who" behind the request. */
	actor_id: string;
	status: string;
	reason?: string | null;
	/** The human/principal that filed the request (NOT the agent — that's `actor_id`). */
	requested_by: string;
	/** ISO timestamp the request was filed (present on list/get responses). */
	filed_at?: string | null;
	/** ISO timestamp the request expires. */
	expires_at?: string | null;
	items: AccessRequestItem[];
	/** Whether the caller can fulfill the request, and the blocking checks if not. */
	evaluation?: AccessRequestEvaluation | null;
}

/** A cursor page of access requests (`GET /access-requests`). */
export interface AccessRequestPage {
	data: AccessRequest[];
	has_more: boolean;
	next_cursor?: string | null;
}

/** A per-item decision sent to the `:decide` verb. */
export interface ItemDecision {
	item_id: string;
	decision: 'approved' | 'denied';
	decision_reason?: string | null;
}

export interface ListAccessRequestsParams {
	status?: string | null;
	actorId?: string | null;
	cursor?: string | null;
	limit?: number;
}

/**
 * List access requests (`GET /access-requests`), cursor-paginated. The Dashboard
 * "Pending requests" card reads a small page of `status=pending` to surface a
 * durable queue — the source of truth for what's still awaiting a human, unlike
 * the rail's transient `access_request.filed` events.
 */
export async function listAccessRequests(
	params: ListAccessRequestsParams = {},
): Promise<AccessRequestPage> {
	const query: Record<string, unknown> = { limit: params.limit ?? 50 };
	if (params.status != null) query.status = params.status;
	if (params.actorId != null) query.actor_id = params.actorId;
	if (params.cursor != null) query.cursor = params.cursor;
	try {
		return await apiRequest<AccessRequestPage>(OpenAPI, {
			method: 'GET',
			url: '/access-requests',
			query,
		});
	} catch (error) {
		throw toRailError(error, 'Failed to load access requests.');
	}
}

/** Fetch a single access request (we need its item ids to decide). */
export async function getAccessRequest(requestId: string): Promise<AccessRequest> {
	try {
		return await apiRequest<AccessRequest>(OpenAPI, {
			method: 'GET',
			url: '/access-requests/{request_id}',
			path: { request_id: requestId },
			errors: { 404: 'Access request not found.' },
		});
	} catch (error) {
		throw toRailError(error, 'Failed to load the access request.');
	}
}

/** Decide (approve/deny) items on an access request. */
export async function decideAccessRequest(
	requestId: string,
	items: ItemDecision[],
): Promise<AccessRequest> {
	try {
		return await apiRequest<AccessRequest>(OpenAPI, {
			method: 'POST',
			url: '/access-requests/{request_id}:decide',
			path: { request_id: requestId },
			body: { items },
			mediaType: 'application/json',
			errors: { 404: 'Access request not found.' },
		});
	} catch (error) {
		throw toRailError(error, 'Failed to record the decision.');
	}
}

/**
 * Decide every still-pending item on a request with one verdict. The rail row
 * exposes a single Approve / Deny choice per filed event, so we fan that verdict
 * out across the request's pending items. Returns the updated request.
 *
 * If no items are pending (already decided elsewhere), this is a no-op fetch —
 * the caller can treat the returned status as the source of truth.
 */
export async function decideAllPending(
	requestId: string,
	decision: 'approved' | 'denied',
	reason?: string | null,
): Promise<AccessRequest> {
	const current = await getAccessRequest(requestId);
	const pending = current.items.filter((i) => i.status === 'pending');
	if (pending.length === 0) return current;
	const decisions: ItemDecision[] = pending.map((i) => ({
		item_id: i.id,
		decision,
		decision_reason: reason ?? null,
	}));
	return decideAccessRequest(requestId, decisions);
}

/**
 * A human-readable label for an item's target. The item is a fully specified
 * `{resource_type, action, resource_id | resource_reference, to_*}`; we surface
 * the most identifying string available — an explicit id, an api-reference
 * triple, or the resource_type as a last resort.
 */
export function itemTargetLabel(item: AccessRequestItem): string {
	if (item.resource_id) return item.resource_id;
	const ref = item.resource_reference;
	if (ref) {
		const vendor = typeof ref.vendor === 'string' ? ref.vendor : undefined;
		const name = typeof ref.name === 'string' ? ref.name : undefined;
		const version = typeof ref.version === 'string' ? ref.version : undefined;
		const parts = [vendor, name].filter(Boolean).join('/');
		if (parts) return version ? `${parts}@${version}` : parts;
		const apiRef = typeof ref.api_reference === 'string' ? ref.api_reference : undefined;
		if (apiRef) return apiRef;
	}
	return item.resource_type;
}

/**
 * True when the item targets a SPECIFIC resource (a concrete `resource_id`) —
 * the webapp's blue "specific credential/resource" accent — vs a broad action
 * grant (the orange "action/assignment" accent).
 */
export function isSpecificResource(item: AccessRequestItem): boolean {
	return Boolean(item.resource_id);
}

/**
 * True when the item grants a PLATFORM SCOPE (a coarse capability like
 * `capabilities:execute` bound to the actor) rather than a per-resource grant.
 * For these items `resource_id` IS the scope string the backend grants on
 * approval (`EffectApplicator._apply_scope_grant`).
 */
export function isScopeGrant(item: AccessRequestItem): boolean {
	return item.resource_type === 'scope' && item.action === 'grant';
}

/** The platform-scope string a `scope.grant` item grants (its `resource_id`). */
export function scopeLabel(item: AccessRequestItem): string {
	return item.resource_id ?? item.resource_type;
}

/**
 * True when an item's permission `rules` will ACTUALLY be enforced on approval.
 * Broker rules are keyed per `(toolkit_id, credential_id)`, so only a
 * `credential.bind` has a key to apply them to — the backend mirrors this and
 * rejects rules on any other item type (`RulesNotSupportedForBindError`). The
 * single source of truth for "rules enforce on this item type" so the card and
 * any future caller never render an allowlist that won't apply.
 */
export function rulesAreEnforceable(item: AccessRequestItem): boolean {
	return item.resource_type === 'credential' && item.action === 'bind';
}

/** Coerce the loosely-typed `rules` JSON into typed `PermissionRule`s, dropping malformed entries. */
export function parseItemRules(item: AccessRequestItem): PermissionRule[] {
	if (!Array.isArray(item.rules)) return [];
	const out: PermissionRule[] = [];
	for (const raw of item.rules) {
		if (!raw || typeof raw !== 'object') continue;
		const r = raw as Record<string, unknown>;
		const effect = r.effect;
		if (effect !== 'allow' && effect !== 'deny' && effect !== 'require-approval') continue;
		// Defensive: `methods`/`operations` are loosely-typed JSON — keep only the
		// string elements so downstream rendering (keys, chip text) and the
		// `ruleSummary` counts never see numbers/objects.
		const strings = (v: unknown): string[] | null =>
			Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : null;
		out.push({
			effect,
			methods: strings(r.methods),
			path: typeof r.path === 'string' ? r.path : null,
			operations: strings(r.operations),
		});
	}
	return out;
}

/**
 * True when a rule is an UNRESTRICTED allow — effect `allow` with no methods,
 * path, or operations to constrain it. Under the broker's first-match-wins,
 * default-deny evaluation such a rule matches every request, so it grants
 * blanket access. The API now rejects these (a condition-less `allow` is a 422),
 * but a reviewer can still encounter one on a historical request, so the UI
 * flags it loudly rather than rendering it like an innocuous allow.
 */
export function isUnrestrictedAllow(rule: PermissionRule): boolean {
	return (
		rule.effect === 'allow' && !rule.methods?.length && !rule.path && !rule.operations?.length
	);
}

/**
 * A short, screen-reader-friendly sentence describing what a rule set grants —
 * e.g. "Allows GET, POST on 3 operations". Used as the `aria-label` so SR users
 * get the gist without parsing individual chips.
 *
 * Restrictive rules (`deny` / `require-approval`) are summarised FIRST so an SR
 * user hears what is blocked before the (often longer) allow enumeration — the
 * block is the security-critical signal. An UNRESTRICTED allow is the other
 * security-critical signal, so it is called out explicitly as "unrestricted".
 */
export function ruleSummary(rules: PermissionRule[]): string {
	if (rules.length === 0) return 'No operation restrictions — full access to the resource.';
	const ordered = [...rules].sort(
		(a, b) => (a.effect === 'allow' ? 1 : 0) - (b.effect === 'allow' ? 1 : 0),
	);
	const parts = ordered.map((rule) => {
		// An unrestricted allow matches everything — surface that danger plainly
		// instead of the bland "Allows all requests".
		if (isUnrestrictedAllow(rule)) return 'Allows ANY request (unrestricted)';
		const verb =
			rule.effect === 'allow'
				? 'Allows'
				: rule.effect === 'deny'
					? 'Blocks'
					: 'Requires approval for';
		const bits: string[] = [];
		if (rule.methods?.length) bits.push(rule.methods.join(', '));
		if (rule.operations?.length) {
			bits.push(
				`${rule.operations.length} operation${rule.operations.length === 1 ? '' : 's'}`,
			);
		}
		const head = bits.length ? `${verb} ${bits.join(' on ')}` : `${verb} all requests`;
		// Path is a separate scope, not another thing the methods/ops act "on" —
		// append it with its own clause so the meaning stays unambiguous.
		return rule.path ? `${head}, scoped to path ${rule.path}` : head;
	});
	return parts.join('; ') + '.';
}
