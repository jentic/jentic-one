/**
 * Provisioning-plan fulfilment — the operator side of a `--provision` request.
 *
 * An agent can file a *provisioning plan*: an access request whose items describe
 * the whole path to first execution rather than a single last-mile binding:
 *
 *   credential:provision  — a placeholder: create a credential for the API
 *   credential:bind       — bind the AGENT directly to the (new) credential,
 *                           with the permission rules (or a shared rule set)
 *                           that govern the pair
 *
 * The `credential:provision` item is inert on the backend (the effect applicator
 * never executes it). A human fulfils it here by calling the existing credential
 * create endpoints, writes the resulting id back onto the downstream
 * `credential:bind` item via `:amend` (`resource_id`), then approves the whole
 * request. The `credential:bind` effect does the real wiring on approval.
 *
 * (Historical note: plans used to be 4-item chains that also created and bound a
 * toolkit — `toolkit:create` / `toolkit:bind` are retired verbs the server now
 * rejects. Decided historical rows may still carry them; render, never file.)
 *
 * This module is the pure classification/shape layer; the React wizard drives
 * the actual create/amend/decide calls step by step.
 */
import type { AccessRequest, AccessRequestItem } from '@/shared/lib/accessRequests';

/** A provisioning-plan item type the wizard fulfils out-of-band (not a real effect). */
export const FULFILMENT_ITEM_TYPES = new Set(['credential:provision']);

/** `resource_type:action` key for an item. */
export function itemKey(item: AccessRequestItem): string {
	return `${item.resource_type}:${item.action}`;
}

/**
 * True when a request is a provisioning plan — it carries at least one
 * fulfilment-only intent (`credential:provision`). These requests must be
 * decided through the fulfilment wizard (create → amend → approve), not the
 * plain approve/deny dialog, which would approve the inert placeholder into a
 * recorded no-op and leave the bind item unfulfilled.
 */
export function isProvisioningPlan(request: AccessRequest): boolean {
	return request.items.some((it) => FULFILMENT_ITEM_TYPES.has(itemKey(it)));
}

/** The single item of a given `resource_type:action`, or undefined. */
export function findItem(
	request: AccessRequest,
	resourceType: string,
	action: string,
): AccessRequestItem | undefined {
	return request.items.find((it) => it.resource_type === resourceType && it.action === action);
}

/** The API reference `{vendor,name,version}` carried by a plan's items. */
export interface PlanApiReference {
	vendor: string;
	name?: string;
	version?: string;
}

/**
 * Normalize an API vendor/name field to its canonical slug form.
 *
 * Mirror of the backend's `slugify_api_field`: lowercase, strip, collapse
 * runs of non-`[a-z0-9-]` to a single hyphen, trim hyphens, truncate.
 * Plan items carry the reference as the agent filed it (raw domains like
 * `httpbin.org`), while stored rows are slugified on write — any exact-match
 * server-side filter (e.g. the credential list's `vendor` param) needs the
 * slug, not the raw value.
 */
export function slugifyApiField(value: string): string {
	return value
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9-]+/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 100);
}

/**
 * Extract the API reference the plan is about, preferring the
 * `credential:provision` carrier (present on every plan) and falling back to
 * the `credential:bind` item's reference.
 */
export function planApiReference(request: AccessRequest): PlanApiReference | null {
	const carriers = [
		findItem(request, 'credential', 'provision'),
		findItem(request, 'credential', 'bind'),
	];
	for (const carrier of carriers) {
		const ref = carrier?.resource_reference;
		if (!ref) continue;
		const vendor = typeof ref.vendor === 'string' ? ref.vendor : undefined;
		if (!vendor) continue;
		return {
			vendor,
			name: typeof ref.name === 'string' ? ref.name : undefined,
			version: typeof ref.version === 'string' ? ref.version : undefined,
		};
	}
	return null;
}

/**
 * The agent-declared credential auth type on the `credential:provision` item
 * (`security_scheme`), used to pre-select the credential form. Returns
 * `no_auth` for a no-auth plan (or null if there's no provision item at all).
 */
export function planAuthType(request: AccessRequest): string | null {
	const prov = findItem(request, 'credential', 'provision');
	if (!prov?.resource_reference) return null;
	const scheme = prov.resource_reference.security_scheme;
	return typeof scheme === 'string' ? scheme : null;
}

/**
 * True when the plan needs no *operator-entered* credential — either there's no
 * `credential:provision` item at all, or it declares `security_scheme=no_auth`
 * (the API is called without authentication). In both cases the wizard skips
 * the manual credential step; for a no-auth plan it auto-creates a NO_AUTH
 * credential at approval so the `credential:bind` effect still has a credential
 * to attach the agent binding + rules to.
 */
export function planIsNoAuth(request: AccessRequest): boolean {
	const prov = findItem(request, 'credential', 'provision');
	if (prov === undefined) return true;
	const scheme = prov.resource_reference?.security_scheme;
	return scheme === 'no_auth';
}

/**
 * The ordered fulfilment steps a wizard walks for a plan. Each step maps to one
 * concrete operator action; `credentialProvision` (create the credential) is
 * omitted for a no-auth plan whose credential is auto-created at approval.
 * `credentialBind` is where the operator confirms the binding's permission
 * rules before the final review.
 */
export type PlanStep = 'credentialProvision' | 'credentialBind' | 'review';

export function planSteps(request: AccessRequest): PlanStep[] {
	const steps: PlanStep[] = [];
	if (!planIsNoAuth(request)) steps.push('credentialProvision');
	steps.push('credentialBind', 'review');
	return steps;
}

/**
 * The bind items that actually WIRE access for a plan: each `credential:bind`
 * binds the agent directly to a credential (+ rules). All must be approved for
 * the agent to be able to call the API(s).
 */
function planBindItems(request: AccessRequest): AccessRequestItem[] {
	return request.items.filter((it) => it.resource_type === 'credential' && it.action === 'bind');
}

/**
 * True only when a plan reached a genuinely executable state: every access-wiring
 * `credential:bind` item is approved. The request-level `partially_approved` is
 * NOT success — a denied bind means the agent still can't call that API even if
 * the inert provision placeholder was approved. Guards against reporting a
 * misleading "Access granted".
 */
export function isPlanGranted(request: AccessRequest): boolean {
	const binds = planBindItems(request);
	if (binds.length === 0) return false;
	return binds.every((it) => it.status === 'approved');
}

/** The first denial reason among a plan's items, for surfacing why it wasn't granted. */
export function planDenialReason(request: AccessRequest): string | null {
	const denied = request.items.find(
		(it) => it.status === 'denied' && (it.decision_reason ?? '').trim() !== '',
	);
	return denied?.decision_reason ?? null;
}

// ── Multi-chain composites ────────────────────────────────────────────────────
//
// A composite request can carry SEVERAL provisioning chains (one per
// `--provision` API) plus plain items (reference binds to existing
// credentials, scope grants). Items are grouped into chains by the API
// reference they carry — never by position, which the server does not
// guarantee.

/** One provisioning chain: the provision intent + agent↔credential bind for a single API. */
export interface PlanChain {
	/** Canonical `vendor/name[/version]` key the chain's items share. */
	key: string;
	apiRef: PlanApiReference;
	provision?: AccessRequestItem;
	credentialBind?: AccessRequestItem;
}

/** A composite request split into its provisioning chains and everything else. */
export interface PlanShape {
	chains: PlanChain[];
	/** Items outside any chain: plain binds to existing credentials, scope
	 * grants… The wizard surfaces them on the review step and decides them with
	 * the chains, so one composite request is decided in one sitting. */
	extras: AccessRequestItem[];
}

/** The `{vendor,name,version}` of an item's reference, or null. */
function itemApiRef(item: AccessRequestItem): PlanApiReference | null {
	const ref = item.resource_reference;
	if (!ref || typeof ref.vendor !== 'string' || ref.vendor === '') return null;
	return {
		vendor: ref.vendor,
		name: typeof ref.name === 'string' ? ref.name : undefined,
		version: typeof ref.version === 'string' ? ref.version : undefined,
	};
}

function refKeyOf(ref: PlanApiReference): string {
	return [ref.vendor, ref.name ?? '', ref.version ?? ''].join('/');
}

/**
 * Group a request's items into provisioning chains keyed by API reference,
 * plus the plain items outside any chain.
 *
 * Legacy fallback: requests filed before composite support carry ONE chain
 * whose `credential:bind` has no reference. When there is exactly one chain
 * and exactly one unmatched, reference-less `credential:bind`, it is adopted
 * into that chain — preserving the old single-plan behavior. With several
 * chains no such guess is safe, so an unattributable bind stays an extra
 * (the wizard won't silently wire it to the wrong API).
 */
export function planChains(request: AccessRequest): PlanShape {
	const chains = new Map<string, PlanChain>();
	const extras: AccessRequestItem[] = [];
	const unmatchedCredentialBinds: AccessRequestItem[] = [];

	// Pass 1: `credential:provision` intents define the chains (in item order).
	for (const it of request.items) {
		if (!FULFILMENT_ITEM_TYPES.has(itemKey(it))) continue;
		const ref = itemApiRef(it);
		if (!ref) continue;
		const key = refKeyOf(ref);
		const chain = chains.get(key) ?? { key, apiRef: ref };
		chain.provision ??= it;
		chains.set(key, chain);
	}

	// Pass 2: attach binds to their chain by reference; collect the rest.
	for (const it of request.items) {
		if (FULFILMENT_ITEM_TYPES.has(itemKey(it))) {
			if (!itemApiRef(it)) extras.push(it); // malformed intent: no reference
			continue;
		}
		const key = itemKey(it);
		const ref = itemApiRef(it);
		const chain = ref ? chains.get(refKeyOf(ref)) : undefined;
		if (chain && key === 'credential:bind' && chain.credentialBind === undefined) {
			chain.credentialBind = it;
		} else if (key === 'credential:bind' && !ref) {
			unmatchedCredentialBinds.push(it);
		} else {
			extras.push(it);
		}
	}

	const ordered = [...chains.values()];
	// Legacy single-chain adoption (see docstring).
	if (
		ordered.length === 1 &&
		unmatchedCredentialBinds.length === 1 &&
		ordered[0].credentialBind === undefined
	) {
		ordered[0].credentialBind = unmatchedCredentialBinds[0];
	} else {
		extras.push(...unmatchedCredentialBinds);
	}
	return { chains: ordered, extras };
}

/** Per-chain variant of {@link planAuthType}. */
export function chainAuthType(chain: PlanChain): string | null {
	const scheme = chain.provision?.resource_reference?.security_scheme;
	return typeof scheme === 'string' ? scheme : null;
}

/** Per-chain variant of {@link planIsNoAuth}. */
export function chainIsNoAuth(chain: PlanChain): boolean {
	if (chain.provision === undefined) return true;
	return chainAuthType(chain) === 'no_auth';
}

/** All items belonging to a chain (for building per-chain decisions). */
export function chainItems(chain: PlanChain): AccessRequestItem[] {
	return [chain.provision, chain.credentialBind].filter(
		(it): it is AccessRequestItem => it !== undefined,
	);
}
