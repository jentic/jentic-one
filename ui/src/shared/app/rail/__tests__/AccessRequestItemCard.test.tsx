import { describe, expect, it } from 'vitest';
import { render, screen, userEvent, within } from '@/__tests__/test-utils';
import { AccessRequestItemCard } from '@/shared/app/rail/AccessRequestItemCard';
import type { AccessRequestItem } from '@/shared/lib';

const noop = () => {};

function item(over: Partial<AccessRequestItem> = {}): AccessRequestItem {
	return {
		id: 'ari_x',
		resource_type: 'credential',
		action: 'bind',
		status: 'pending',
		...over,
	};
}

function renderCard(over: Partial<AccessRequestItem> = {}, denying = false) {
	return render(
		<AccessRequestItemCard
			item={item(over)}
			denying={denying}
			reason=""
			onApprove={noop}
			onStartDeny={noop}
			onCancelDeny={noop}
			onReasonChange={noop}
		/>,
	);
}

describe('AccessRequestItemCard — scope.grant treatment', () => {
	it('labels a scope grant as a Platform scope and shows the scope string', () => {
		renderCard({
			resource_type: 'scope',
			action: 'grant',
			resource_id: 'capabilities:execute',
		});
		// The scope string is the headline; "Platform scope" is the badge + subtitle.
		expect(screen.getByRole('heading', { name: 'capabilities:execute' })).toBeInTheDocument();
		expect(screen.getAllByText('Platform scope').length).toBeGreaterThanOrEqual(1);
		// A scope grant is coarse — it must NOT render the operations allow/block list.
		expect(screen.queryByText(/Operations granted/i)).not.toBeInTheDocument();
		// The deny affordance is keyed off the scope label.
		expect(
			screen.getByRole('button', { name: 'Deny capabilities:execute' }),
		).toBeInTheDocument();
	});
});

describe('AccessRequestItemCard — credential.bind operations', () => {
	it('renders a read-only allow/block operations summary from the item rules', () => {
		renderCard({
			resource_type: 'credential',
			action: 'bind',
			rules: [
				{ effect: 'allow', methods: ['GET'], operations: ['repos/get', 'repos/list'] },
				{ effect: 'deny', methods: ['DELETE'] },
			],
		});
		expect(screen.getByText(/Operations granted/i)).toBeInTheDocument();
		expect(screen.getByText('Allow')).toBeInTheDocument();
		expect(screen.getByText('Block')).toBeInTheDocument();
		expect(screen.getByText('repos/get')).toBeInTheDocument();
		expect(screen.getByText('repos/list')).toBeInTheDocument();
	});

	it('defers a long operation list to the full-view dialog', async () => {
		const user = userEvent.setup();
		const ops = ['a', 'b', 'c', 'd', 'e', 'f'];
		renderCard({
			rules: [{ effect: 'allow', operations: ops }],
		});
		// Only the first 4 ops preview inline; the rest are summarised as "+N more"
		// and the full set is reachable via the dialog (never dumped into the card).
		expect(screen.getByText('a')).toBeInTheDocument();
		expect(screen.getByText('d')).toBeInTheDocument();
		expect(screen.queryByText('e')).not.toBeInTheDocument();
		expect(screen.getByText('+2 more')).toBeInTheDocument();
		const viewAll = screen.getByRole('button', { name: /view all 6/i });
		await user.click(viewAll);
		// The dialog lists every operation, including the ones hidden inline.
		const dialog = screen.getByRole('dialog');
		expect(within(dialog).getByText('e')).toBeInTheDocument();
		expect(within(dialog).getByText('f')).toBeInTheDocument();
	});

	it('omits the operations summary when the item has no rules', () => {
		renderCard({ resource_type: 'toolkit', action: 'use' });
		expect(screen.queryByText(/Operations granted/i)).not.toBeInTheDocument();
	});

	it('exposes a screen-reader summary of the granted rules', () => {
		renderCard({
			rules: [{ effect: 'allow', methods: ['GET', 'POST'], operations: ['x', 'y', 'z'] }],
		});
		// The whole block is labelled so SR users hear the gist without parsing chips.
		expect(screen.getByLabelText('Allows GET, POST on 3 operations.')).toBeInTheDocument();
	});
});

describe('AccessRequestItemCard — non-enforceable rules', () => {
	it('hides the allowlist and shows a notice for a toolkit.bind carrying rules', () => {
		// Broker rules key per credential, so a toolkit.bind (agent↔toolkit) can't
		// enforce them — we must NOT render an allowlist that won't apply.
		renderCard({
			resource_type: 'toolkit',
			action: 'bind',
			resource_id: 'tk_target',
			rules: [{ effect: 'allow', methods: ['GET'] }],
		});
		expect(screen.queryByText(/Operations granted/i)).not.toBeInTheDocument();
		expect(screen.getByText(/will not be enforced/i)).toBeInTheDocument();
	});

	it('still renders the allowlist for a credential.bind carrying rules', () => {
		renderCard({
			resource_type: 'credential',
			action: 'bind',
			rules: [{ effect: 'allow', methods: ['GET'] }],
		});
		expect(screen.getByText(/Operations granted/i)).toBeInTheDocument();
		expect(screen.queryByText(/will not be enforced/i)).not.toBeInTheDocument();
	});
});
