import { describe, expect, it } from 'vitest';
import { denySummary, heldCallApiTitle } from '@/modules/agents/lib/heldCalls';

describe('heldCallApiTitle', () => {
	const call = { api_vendor: 'api.stripe.com', api_name: 'payments', api_version: '2024-01-01' };
	const tile = (apiName: string | null, version: string | null, title: string) => ({
		vendor: 'api.stripe.com',
		apiName,
		version,
		title,
	});

	it('names the API as its row does, preferring the exact version', () => {
		expect(
			heldCallApiTitle(call, [
				tile('payments', '2023-01-01', 'Stripe (old)'),
				tile('payments', '2024-01-01', 'Stripe'),
			]),
		).toBe('Stripe');
		expect(heldCallApiTitle(call, [tile('payments', '2023-01-01', 'Stripe (old)')])).toBe(
			'Stripe (old)',
		);
	});

	it('falls back to a vendor-wide binding, then to the vendor itself', () => {
		expect(heldCallApiTitle(call, [tile(null, null, 'Stripe, every API')])).toBe(
			'Stripe, every API',
		);
		expect(heldCallApiTitle(call, [tile('billing', '1', 'Stripe Billing')])).toBe(
			'api.stripe.com',
		);
		expect(heldCallApiTitle(call, [])).toBe('api.stripe.com');
	});
});

describe('denySummary', () => {
	it('reads as a plain success when every call was denied', () => {
		expect(denySummary({ denied: 3, alreadyDecided: 0, failed: 0 }, 'billing-ops')).toEqual({
			title: 'Denied 3 calls from billing-ops',
			variant: 'success',
		});
		expect(denySummary({ denied: 1, alreadyDecided: 0, failed: 0 }, 'billing-ops').title).toBe(
			'Denied 1 call from billing-ops',
		);
	});

	it('says which calls someone else decided first, without calling it a failure', () => {
		expect(denySummary({ denied: 1, alreadyDecided: 1, failed: 0 }, 'billing-ops')).toEqual({
			title: 'Denied 1 of 2 calls',
			description: '1 call was already decided by someone else.',
			variant: 'info',
		});
		expect(denySummary({ denied: 0, alreadyDecided: 2, failed: 0 }, 'billing-ops')).toEqual({
			title: 'Already decided',
			description: '2 calls were already decided by someone else.',
			variant: 'info',
		});
	});

	it('asks for another try when a deny failed', () => {
		expect(denySummary({ denied: 2, alreadyDecided: 0, failed: 1 }, 'billing-ops')).toEqual({
			title: 'Denied 2 of 3 calls',
			description: '1 call couldn’t be denied and is still selected. Try again.',
			variant: 'warning',
		});
		expect(denySummary({ denied: 0, alreadyDecided: 1, failed: 1 }, 'billing-ops')).toEqual({
			title: 'Couldn’t deny 2 calls',
			description:
				'1 call was already decided by someone else. 1 call couldn’t be denied and is still selected. Try again.',
			variant: 'error',
		});
	});
});
