import { describe, expect, it } from 'vitest';
import {
	FULFILMENT_ITEM_TYPES,
	findItem,
	isProvisioningPlan,
	itemKey,
	planApiReference,
	planAuthType,
	planIsNoAuth,
	planSteps,
} from '@/shared/lib/provisioningPlan';
import type { AccessRequest, AccessRequestItem } from '@/shared/lib/accessRequests';

function item(partial: Partial<AccessRequestItem>): AccessRequestItem {
	return {
		id: partial.id ?? 'arqi_x',
		resource_type: partial.resource_type ?? 'toolkit',
		action: partial.action ?? 'create',
		status: partial.status ?? 'pending',
		resource_reference: partial.resource_reference ?? null,
		resource_id: partial.resource_id ?? null,
		to_id: partial.to_id ?? null,
		rules: partial.rules ?? null,
	};
}

function plan(items: AccessRequestItem[]): AccessRequest {
	return {
		id: 'areq_1',
		actor_id: 'agnt_1',
		status: 'pending',
		requested_by: 'agnt_1',
		items,
	};
}

const REF = { vendor: 'posthog.com', name: 'posthog-api', version: '1.0.0' };

function fullPlan(): AccessRequest {
	return plan([
		item({ id: 'i1', resource_type: 'toolkit', action: 'create', resource_reference: REF }),
		item({
			id: 'i2',
			resource_type: 'credential',
			action: 'provision',
			resource_reference: { ...REF, security_scheme: 'bearer' },
		}),
		item({ id: 'i3', resource_type: 'credential', action: 'bind' }),
		item({ id: 'i4', resource_type: 'toolkit', action: 'bind', resource_reference: REF }),
	]);
}

describe('provisioningPlan', () => {
	it('recognizes a provisioning plan by its fulfilment intents', () => {
		expect(isProvisioningPlan(fullPlan())).toBe(true);
	});

	it('does not treat a plain toolkit:bind request as a plan', () => {
		const req = plan([
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: REF }),
		]);
		expect(isProvisioningPlan(req)).toBe(false);
	});

	it('extracts the API reference from the toolkit:create item', () => {
		expect(planApiReference(fullPlan())).toEqual({
			vendor: 'posthog.com',
			name: 'posthog-api',
			version: '1.0.0',
		});
	});

	it('reads the declared auth type off credential:provision', () => {
		expect(planAuthType(fullPlan())).toBe('bearer');
	});

	it('detects a no-auth plan (no credential:provision item)', () => {
		const noAuth = plan([
			item({ resource_type: 'toolkit', action: 'create', resource_reference: REF }),
			item({ resource_type: 'credential', action: 'bind' }),
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: REF }),
		]);
		expect(planIsNoAuth(noAuth)).toBe(true);
		expect(planAuthType(noAuth)).toBeNull();
	});

	it('detects a no-auth plan by security_scheme=no_auth on the provision item', () => {
		const noAuth = plan([
			item({ resource_type: 'toolkit', action: 'create', resource_reference: REF }),
			item({
				resource_type: 'credential',
				action: 'provision',
				resource_reference: { ...REF, security_scheme: 'no_auth' },
			}),
			item({ resource_type: 'credential', action: 'bind' }),
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: REF }),
		]);
		expect(planIsNoAuth(noAuth)).toBe(true);
		expect(planAuthType(noAuth)).toBe('no_auth');
	});

	it('orders steps, omitting credentialProvision for a no-auth plan', () => {
		expect(planSteps(fullPlan())).toEqual([
			'toolkitCreate',
			'credentialProvision',
			'credentialBind',
			'toolkitBind',
			'review',
		]);
		const noAuth = plan([
			item({ resource_type: 'toolkit', action: 'create', resource_reference: REF }),
			item({ resource_type: 'credential', action: 'bind' }),
			item({ resource_type: 'toolkit', action: 'bind', resource_reference: REF }),
		]);
		expect(planSteps(noAuth)).toEqual([
			'toolkitCreate',
			'credentialBind',
			'toolkitBind',
			'review',
		]);
	});

	it('finds items by resource_type/action', () => {
		expect(findItem(fullPlan(), 'credential', 'bind')?.id).toBe('i3');
		expect(findItem(fullPlan(), 'scope', 'grant')).toBeUndefined();
	});

	it('exposes item keys and the fulfilment set', () => {
		expect(itemKey(item({ resource_type: 'toolkit', action: 'create' }))).toBe(
			'toolkit:create',
		);
		expect(FULFILMENT_ITEM_TYPES.has('credential:provision')).toBe(true);
		expect(FULFILMENT_ITEM_TYPES.has('credential:bind')).toBe(false);
	});
});
