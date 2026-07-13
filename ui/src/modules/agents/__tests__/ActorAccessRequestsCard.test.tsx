import { describe, it, expect, beforeEach } from 'vitest';
import {
	renderWithProviders,
	screen,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { resetRailEventsStore } from '@/shared/app/rail/mocks/handlers';
import { ActorAccessRequestsCard } from '@/modules/agents/components/ActorAccessRequestsCard';

function renderCard(props: { actorId: string; actorName: string }) {
	return renderWithProviders(
		<>
			<ActorAccessRequestsCard {...props} />
			<Toaster />
		</>,
	);
}

describe('ActorAccessRequestsCard', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
		resetRailEventsStore();
	});

	it("lists the actor's pending access requests with a count", async () => {
		// `ar_1` (actor agnt_active_1) is seeded pending with three items.
		renderCard({ actorId: 'agnt_active_1', actorName: 'support-agent' });

		expect(await screen.findByRole('heading', { name: 'Access requests' })).toBeInTheDocument();
		// Summarized as "<first item> +N more" — three items → "+2 more".
		expect(await screen.findByText(/toolkit · use \+2 more/)).toBeInTheDocument();
		// The reason is shown beneath the summary.
		expect(screen.getByText('agent requested repo:read + secret:read')).toBeInTheDocument();
		// Count badge reflects the number of pending requests.
		expect(screen.getByText('1')).toBeInTheDocument();
	});

	it('only shows requests filed by THIS actor (filters by actor_id)', async () => {
		// `ar_2` belongs to agnt_active_2; agnt_active_1 must not see it.
		renderCard({ actorId: 'agnt_active_1', actorName: 'support-agent' });
		await screen.findByText(/toolkit · use \+2 more/);
		expect(screen.queryByText('agent needs to call the payments API')).not.toBeInTheDocument();
	});

	it('defaults to pending and reveals decided history via the status filter', async () => {
		const user = userEvent.setup();
		// agnt_active_1 has one pending (ar_1) + one approved (ar_3) + one denied
		// (ar_4) request seeded in the rail store.
		renderCard({ actorId: 'agnt_active_1', actorName: 'support-agent' });

		// Pending by default: only the pending request, and decided ones are hidden.
		await screen.findByText(/toolkit · use \+2 more/);
		expect(
			screen.queryByText('agent needed read access to the analytics toolkit'),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText('agent requested org:admin — out of policy'),
		).not.toBeInTheDocument();

		// Approved filter: the approved request appears, pending/denied do not.
		await user.click(screen.getByRole('button', { name: 'Approved' }));
		expect(
			await screen.findByText('agent needed read access to the analytics toolkit'),
		).toBeInTheDocument();
		expect(
			screen.queryByText('agent requested repo:read + secret:read'),
		).not.toBeInTheDocument();

		// Denied filter: the denied request appears.
		await user.click(screen.getByRole('button', { name: 'Denied' }));
		expect(
			await screen.findByText('agent requested org:admin — out of policy'),
		).toBeInTheDocument();

		// All filter: every status shows, with a per-row status badge.
		await user.click(screen.getByRole('button', { name: 'All' }));
		expect(await screen.findByText(/toolkit · use \+2 more/)).toBeInTheDocument();
		expect(
			screen.getByText('agent needed read access to the analytics toolkit'),
		).toBeInTheDocument();
		expect(screen.getByText('approved')).toBeInTheDocument();
		expect(screen.getByText('denied')).toBeInTheDocument();
	});

	it('shows a filter-aware empty state when nothing matches', async () => {
		const user = userEvent.setup();
		// agnt_active_2 has only a pending request — no approved history.
		renderCard({ actorId: 'agnt_active_2', actorName: 'payments-agent' });
		await screen.findByText(/toolkit · use$/);

		await user.click(screen.getByRole('button', { name: 'Approved' }));
		expect(await screen.findByText('No access requests')).toBeInTheDocument();
		expect(
			screen.getByText('payments-agent has no access requests matching this filter.'),
		).toBeInTheDocument();
	});

	it('shows an honest empty state when the actor has filed nothing pending', async () => {
		// sva_active_1 has no seeded access requests.
		renderCard({ actorId: 'sva_active_1', actorName: 'metrics-exporter' });
		expect(await screen.findByText('No pending access requests')).toBeInTheDocument();
		expect(
			screen.getByText('metrics-exporter has no access requests awaiting a decision.'),
		).toBeInTheDocument();
	});

	it('surfaces an error when the list fails to load', async () => {
		const { worker } = await import('@/mocks/browser');
		worker.use(createErrorHandler('get', '/access-requests', { status: 500 }));

		renderCard({ actorId: 'agnt_active_1', actorName: 'support-agent' });
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('opens the decide dialog from a row and drops the request after deciding', async () => {
		const user = userEvent.setup();
		renderCard({ actorId: 'agnt_active_1', actorName: 'support-agent' });

		await user.click(await screen.findByText(/toolkit · use \+2 more/));

		const dialog = await screen.findByRole('dialog');
		// Wait for the request to load inside the dialog, then approve every
		// pending item, advance to the confirm step, and submit.
		const approveAll = await within(dialog).findByRole('button', { name: 'Approve all' });
		await user.click(approveAll);
		await user.click(within(dialog).getByRole('button', { name: /Review & submit/i }));
		await user.click(within(dialog).getByRole('button', { name: /Confirm decision/i }));

		// Decided → the request leaves the pending queue, so the row disappears
		// and the empty state takes over.
		expect(await screen.findByText('No pending access requests')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderCard({
			actorId: 'agnt_active_1',
			actorName: 'support-agent',
		});
		await screen.findByText(/toolkit · use \+2 more/);
		await checkA11y(container);
	});
});
