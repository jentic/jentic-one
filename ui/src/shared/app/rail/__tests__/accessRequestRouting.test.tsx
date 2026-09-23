import { beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { planIsFulfillable } from '@/shared/lib';
import { AccessRequestDecisionDialog } from '@/shared/app/rail/AccessRequestDecisionDialog';
import { resetProvisioningWizardDrafts } from '@/shared/app/rail/ProvisioningRequestDialog';
import { ACCESS_REQUEST_SHAPES } from '@/shared/app/rail/__tests__/accessRequestShapes';

/**
 * Exhaustive routing coverage for every access-request shape the system can
 * produce: a *fulfillable provisioning plan* (a `credential:provision` chain
 * with a `credential:bind` to wire) must open the setup wizard; everything
 * else — plain single-item requests AND provision-carrying plans the wizard
 * can't drive — opens the plain approve/deny dialog. `AccessRequestDecisionDialog`
 * is the single place that decides this, so we assert its `planIsFulfillable`
 * routing against the full shape catalog.
 */
describe('access-request routing (all shapes)', () => {
	// Wizard drafts are module-scoped; cases share the plan fixtures.
	beforeEach(() => resetProvisioningWizardDrafts());
	it.each(ACCESS_REQUEST_SHAPES.map((s) => [s.title, s] as const))(
		'routes %s to the expected surface',
		(_title, shape) => {
			const expectedWizard = shape.routedTo === 'wizard';
			expect(planIsFulfillable(shape.request)).toBe(expectedWizard);
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
		expect(screen.getByText(/^Choose a credential/)).toBeInTheDocument();
	});

	// The rail only carries the request ID on the event token, so it opens this
	// wrapper in fetch-by-id mode. Before this mode existed the rail rendered the
	// plain dialog directly and a plan dead-ended on "open it from Access
	// Requests" — pin that the fetched plan routes to the wizard too.
	it('fetches by id and routes a provisioning plan to the wizard (rail path)', async () => {
		const plan = ACCESS_REQUEST_SHAPES.find((s) => s.key === 'plan-oauth-pending')!;
		worker.use(http.get('/access-requests/:id', () => HttpResponse.json(plan.request)));
		renderWithProviders(
			<AccessRequestDecisionDialog
				requestId={plan.request.id}
				eventId="evt_rail_1"
				onClose={() => {}}
				onDecided={() => {}}
			/>,
		);
		await waitFor(() => expect(screen.getByText('Set up access')).toBeInTheDocument());
		expect(screen.getByText(/^Choose a credential/)).toBeInTheDocument();
	});

	// A `credential:provision` request whose bind can't join a chain is a plan
	// (`isProvisioningPlan` true) the wizard cannot render — it used to open the
	// wizard, which returned null, and the operator saw nothing happen. It must
	// fall through to the plain approve/deny dialog so binding requests can be
	// decided. Assert it renders the plain dialog rather than an empty surface.
	it('routes a non-fulfillable provisioning plan to the approve/deny dialog', async () => {
		const plan = ACCESS_REQUEST_SHAPES.find((s) => s.key === 'plan-bind-reference-mismatch')!;
		// The plain dialog re-fetches by id; serve the mismatched plan so the
		// surface under test decides the plan's own items, not a seeded stand-in.
		worker.use(http.get('/access-requests/:id', () => HttpResponse.json(plan.request)));
		renderWithProviders(
			<AccessRequestDecisionDialog
				request={plan.request}
				onClose={() => {}}
				onDecided={() => {}}
			/>,
		);
		await waitFor(() =>
			expect(screen.getByText(/Step 1 of 2 · Review each item/)).toBeInTheDocument(),
		);
	});

	it('fetches by id and routes a plain request to the approve/deny dialog (rail path)', async () => {
		const plain = ACCESS_REQUEST_SHAPES.find(
			(s) => s.key === 'credential-bind-reference-pending',
		)!;
		worker.use(http.get('/access-requests/:id', () => HttpResponse.json(plain.request)));
		renderWithProviders(
			<AccessRequestDecisionDialog
				requestId={plain.request.id}
				eventId="evt_rail_2"
				onClose={() => {}}
				onDecided={() => {}}
			/>,
		);
		await waitFor(() =>
			expect(screen.getByText(/Step 1 of 2 · Review each item/)).toBeInTheDocument(),
		);
	});
});
