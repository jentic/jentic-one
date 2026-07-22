/**
 * Provisioning-plan fulfilment — the operator side of a `--provision` request.
 *
 * An agent can file a *provisioning plan*: an access request whose items describe
 * the whole path to first execution rather than a single last-mile binding:
 *
 *   toolkit:create        — a placeholder: create a toolkit that serves the API
 *   credential:provision  — a placeholder: create a credential for the API
 *   credential:bind       — bind the (new) credential to the (new) toolkit + rules
 *   toolkit:bind          — bind the agent to the (new) toolkit
 *
 * The two `*:create` / `*:provision` items are inert on the backend (the effect
 * applicator never executes them). A human fulfils them here by calling the
 * existing create endpoints, writes the resulting ids back onto the downstream
 * bind items via `:amend`, then approves the whole request. The existing
 * `credential:bind` / `toolkit:bind` effects do the real wiring on approval.
 *
 * This module is the pure classification/shape layer; the React wizard drives
 * the actual create/amend/decide calls step by step.
 */
import type { AccessRequest, AccessRequestItem } from '@/shared/lib/accessRequests';

/** A provisioning-plan item type the wizard fulfils out-of-band (not a real effect). */
export const FULFILMENT_ITEM_TYPES = new Set(['toolkit:create', 'credential:provision']);

/** `resource_type:action` key for an item. */
export function itemKey(item: AccessRequestItem): string {
	return `${item.resource_type}:${item.action}`;
}

/**
 * True when a request is a provisioning plan — it carries at least one
 * fulfilment-only intent (`toolkit:create` or `credential:provision`). These
 * requests must be decided through the fulfilment wizard (create → amend →
 * approve), not the plain approve/deny dialog, which would approve the inert
 * placeholders into a recorded no-op and leave the bind items unfulfilled.
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
 * Extract the API reference the plan is about, preferring the toolkit:create
 * item (always present) and falling back to any item carrying a reference.
 */
export function planApiReference(request: AccessRequest): PlanApiReference | null {
	const carrier =
		findItem(request, 'toolkit', 'create') ??
		findItem(request, 'credential', 'provision') ??
		findItem(request, 'toolkit', 'bind');
	const ref = carrier?.resource_reference;
	if (!ref) return null;
	const vendor = typeof ref.vendor === 'string' ? ref.vendor : undefined;
	if (!vendor) return null;
	return {
		vendor,
		name: typeof ref.name === 'string' ? ref.name : undefined,
		version: typeof ref.version === 'string' ? ref.version : undefined,
	};
}

/**
 * The agent-declared credential auth type on the `credential:provision` item
 * (`security_scheme`), used to pre-select the credential form. Absent when the
 * plan is for a no-auth API (no `credential:provision` item at all).
 */
export function planAuthType(request: AccessRequest): string | null {
	const prov = findItem(request, 'credential', 'provision');
	if (!prov?.resource_reference) return null;
	const scheme = prov.resource_reference.security_scheme;
	return typeof scheme === 'string' ? scheme : null;
}

/** True when the plan needs no credential (no `credential:provision` item). */
export function planIsNoAuth(request: AccessRequest): boolean {
	return findItem(request, 'credential', 'provision') === undefined;
}

/**
 * The ordered fulfilment steps a wizard walks for a plan. Each step maps to one
 * concrete operator action; `credentialProvision` is omitted for a no-auth plan.
 */
export type PlanStep =
	'toolkitCreate' | 'credentialProvision' | 'credentialBind' | 'toolkitBind' | 'review';

export function planSteps(request: AccessRequest): PlanStep[] {
	const steps: PlanStep[] = ['toolkitCreate'];
	if (!planIsNoAuth(request)) steps.push('credentialProvision');
	steps.push('credentialBind', 'toolkitBind', 'review');
	return steps;
}
