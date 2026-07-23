import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { isProvisioningPlan } from '@/shared/lib';
import { AccessRequestDecisionDialog } from '@/shared/app/rail/AccessRequestDecisionDialog';
import { ACCESS_REQUEST_SHAPES } from '@/shared/app/rail/__tests__/accessRequestShapes';

/**
 * Exhaustive routing coverage for every access-request shape the system can
 * produce: a *provisioning plan* (carries toolkit:create / credential:provision)
 * must open the setup wizard; everything else opens the plain approve/deny
 * dialog. `AccessRequestDecisionDialog` is the single place that decides this,
 * so we assert its `isProvisioningPlan` routing against the full shape catalog.
 */
describe('access-request routing (all shapes)', () => {
	it.each(ACCESS_REQUEST_SHAPES.map((s) => [s.title, s] as const))(
		'routes %s to the expected surface',
		(_title, shape) => {
			const expectedPlan = shape.routedTo === 'wizard';
			expect(isProvisioningPlan(shape.request)).toBe(expectedPlan);
		},
	);

	it('opens a provisioning plan in the setup wizard', async () => {
		const plan = ACCESS_REQUEST_SHAPES.find((s) => s.key === 'plan-oauth-pending')!;
		renderWithProviders(
			<AccessRequestDecisionDialog
				request={plan.request}
				onClose={() => {}}
				onDecided={() => {}}
			/>,
		);
		// The wizard renders from the request prop (no fetch needed on open).
		await waitFor(() => expect(screen.getByText('Set up access')).toBeInTheDocument());
		expect(screen.getByText('Create a toolkit')).toBeInTheDocument();
	});
});
