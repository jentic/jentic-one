import { describe, expect, it } from 'vitest';
import {
	FULFILMENT_ITEM_TYPES,
	chainAuthType,
	chainIsNoAuth,
	chainItems,
	findItem,
	isPlanGranted,
	isProvisioningPlan,
	itemKey,
	planApiReference,
	planAuthType,
	planChains,
	planDenialReason,
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
		decision_reason: partial.decision_reason ?? null,
	};
}

function plan(items: AccessRequestItem[]): AccessRequest {
	return {
		id: 'areq_1',
		actor_id: 'agnt_1',
		status: 'pending',
		requested_by: 'agnt_1',
		created_by: 'agnt_1',
		approve_url: 'https://app.example.test/access-requests/areq_1',
		filed_at: '2026-07-23T09:00:00Z',
		expires_at: '2026-07-30T09:00:00Z',
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

	describe('isPlanGranted / planDenialReason', () => {
		const bind = (status: string, reason?: string): AccessRequestItem[] => [
			item({ id: 'i1', resource_type: 'toolkit', action: 'create', status: 'approved' }),
			item({ id: 'i3', resource_type: 'credential', action: 'bind', status }),
			item({
				id: 'i4',
				resource_type: 'toolkit',
				action: 'bind',
				status,
				decision_reason: reason ?? null,
			}),
		];

		it('is granted only when BOTH bind items are approved', () => {
			const req = plan(bind('approved'));
			req.status = 'approved';
			expect(isPlanGranted(req)).toBe(true);
		});

		it('is NOT granted when a bind is denied even if aggregate is partially_approved', () => {
			// credential:bind approved, toolkit:bind denied → agent still can't call.
			const req = plan([
				item({ id: 'i1', resource_type: 'toolkit', action: 'create', status: 'approved' }),
				item({ id: 'i3', resource_type: 'credential', action: 'bind', status: 'approved' }),
				item({
					id: 'i4',
					resource_type: 'toolkit',
					action: 'bind',
					status: 'denied',
					decision_reason: 'no toolkit serves it',
				}),
			]);
			req.status = 'partially_approved';
			expect(isPlanGranted(req)).toBe(false);
			expect(planDenialReason(req)).toBe('no toolkit serves it');
		});

		it('is not granted when there are no bind items', () => {
			const req = plan([
				item({ resource_type: 'toolkit', action: 'create', status: 'approved' }),
			]);
			expect(isPlanGranted(req)).toBe(false);
		});
	});

	describe('planChains', () => {
		const REF_A = { vendor: 'slack.com', name: 'api' };
		const REF_B = { vendor: 'googleapis.com', name: 'sheets' };

		const chainFor = (
			ref: { vendor: string; name: string },
			p: string,
		): AccessRequestItem[] => [
			item({
				id: `${p}1`,
				resource_type: 'toolkit',
				action: 'create',
				resource_reference: ref,
			}),
			item({
				id: `${p}2`,
				resource_type: 'credential',
				action: 'provision',
				resource_reference: { ...ref, security_scheme: p === 'a' ? 'api_key' : 'no_auth' },
			}),
			item({
				id: `${p}3`,
				resource_type: 'credential',
				action: 'bind',
				resource_reference: ref,
			}),
			item({
				id: `${p}4`,
				resource_type: 'toolkit',
				action: 'bind',
				resource_reference: ref,
			}),
		];

		it('groups a composite into per-API chains plus extras, by reference not position', () => {
			const req = plan([
				// Interleave the two chains + plain items to prove grouping is by ref.
				...chainFor(REF_A, 'a'),
				item({
					id: 'x1',
					resource_type: 'toolkit',
					action: 'bind',
					resource_reference: { vendor: 'github.com', name: 'api' },
				}),
				...chainFor(REF_B, 'b'),
				item({
					id: 'x2',
					resource_type: 'scope',
					action: 'grant',
					resource_id: 'catalog:import',
				}),
			]);
			const shape = planChains(req);
			expect(shape.chains.map((c) => c.apiRef.vendor)).toEqual([
				'slack.com',
				'googleapis.com',
			]);
			const [a, b] = shape.chains;
			expect(a.credentialBind?.id).toBe('a3');
			expect(a.toolkitBind?.id).toBe('a4');
			expect(b.credentialBind?.id).toBe('b3');
			expect(chainAuthType(a)).toBe('api_key');
			expect(chainIsNoAuth(a)).toBe(false);
			expect(chainIsNoAuth(b)).toBe(true);
			expect(chainItems(a).map((it) => it.id)).toEqual(['a1', 'a2', 'a3', 'a4']);
			// The plain bind to a non-chain API and the scope grant stay extras.
			expect(shape.extras.map((it) => it.id)).toEqual(['x1', 'x2']);
		});

		it('adopts a single reference-less credential:bind into a single chain (legacy shape)', () => {
			// Requests filed before composite support: the credential:bind has no
			// reference. With exactly one chain the attribution is unambiguous.
			const req = plan([
				item({
					id: 'l1',
					resource_type: 'toolkit',
					action: 'create',
					resource_reference: REF_A,
				}),
				item({
					id: 'l2',
					resource_type: 'credential',
					action: 'provision',
					resource_reference: { ...REF_A, security_scheme: 'bearer' },
				}),
				item({ id: 'l3', resource_type: 'credential', action: 'bind' }),
				item({
					id: 'l4',
					resource_type: 'toolkit',
					action: 'bind',
					resource_reference: REF_A,
				}),
			]);
			const shape = planChains(req);
			expect(shape.chains).toHaveLength(1);
			expect(shape.chains[0].credentialBind?.id).toBe('l3');
			expect(shape.extras).toEqual([]);
		});

		it('never guesses which chain owns a reference-less bind when there are several', () => {
			const req = plan([
				...chainFor(REF_A, 'a').filter((it) => it.id !== 'a3'),
				...chainFor(REF_B, 'b').filter((it) => it.id !== 'b3'),
				item({ id: 'orphan', resource_type: 'credential', action: 'bind' }),
			]);
			const shape = planChains(req);
			expect(shape.chains).toHaveLength(2);
			expect(shape.chains.every((c) => c.credentialBind === undefined)).toBe(true);
			expect(shape.extras.map((it) => it.id)).toEqual(['orphan']);
		});

		it('yields no chains for a plain (non-plan) request', () => {
			const req = plan([
				item({ resource_type: 'toolkit', action: 'bind', resource_reference: REF_A }),
			]);
			const shape = planChains(req);
			expect(shape.chains).toEqual([]);
			expect(shape.extras).toHaveLength(1);
		});
	});
});
