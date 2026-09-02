import { describe, it, expect, beforeEach } from 'vitest';
import { renderWithProviders, screen, within, userEvent, checkA11y } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetSettingsStore } from '@/modules/settings/mocks/handlers';
import { OAuthClientsSection } from '@/modules/settings/pages/OAuthClientsSection';

function renderSection(route = '/settings') {
	return renderWithProviders(
		<>
			<OAuthClientsSection />
			<Toaster />
		</>,
		{ route },
	);
}

describe('OAuthClientsSection', () => {
	beforeEach(() => {
		setToken('test-token');
		resetSettingsStore();
	});

	it('lists clients with badges and the per-client active-grant count', async () => {
		renderSection();

		// The admin-registered confidential client, with its §4.8 grant count.
		expect(await screen.findByText('Internal Dashboard')).toBeInTheDocument();
		expect(screen.getByText('Active grants:')).toBeInTheDocument();
		expect(screen.getByText('2')).toBeInTheDocument();
		// Pending/denied rows are inactive → hidden from the default clients list.
		expect(screen.queryByText('Sketchy Tool')).not.toBeInTheDocument();
	});

	it('carries the pending count on the Approval queue tab label', async () => {
		renderSection();
		// One seeded pending registration → the tab badge shows 1.
		const tab = await screen.findByRole('tab', { name: /Approval queue/ });
		expect(await within(tab).findByText('1')).toBeInTheDocument();
	});

	it('deep-links to the queue via ?tab=queue (the rail Review action)', async () => {
		renderSection('/settings?tab=queue');
		// The pending DCR registration renders with its badges (scoped to the
		// row heading — "Pending" also names the queue's filter button).
		const heading = await screen.findByText('Cursor');
		const row = heading.closest('h3');
		expect(row).not.toBeNull();
		expect(within(row as HTMLElement).getByText('Public')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('DCR')).toBeInTheDocument();
		expect(within(row as HTMLElement).getByText('Pending')).toBeInTheDocument();
		expect(screen.getByText('com.cursor.ide')).toBeInTheDocument();
	});

	it('approves a pending registration and empties the queue (D7 pending→approved)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Approve' }));

		expect(await screen.findByText('Cursor approved')).toBeInTheDocument();
		expect(await screen.findByText('No pending registrations')).toBeInTheDocument();

		// The approved client now shows on the Clients tab, active.
		await user.click(screen.getByRole('tab', { name: /Clients/ }));
		expect(await screen.findByText('Cursor')).toBeInTheDocument();
	});

	it('denies a pending registration via the reason dialog (D7 pending→denied)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		await user.click(screen.getByRole('button', { name: 'Deny' }));
		const dialog = await screen.findByRole('dialog');
		await user.type(
			within(dialog).getByLabelText('Reason (optional)'),
			'unknown redirect URIs',
		);
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		expect(await screen.findByText('Cursor denied')).toBeInTheDocument();
		expect(await screen.findByText('No pending registrations')).toBeInTheDocument();
	});

	it('recovers a denied client: the Denied filter re-offers Approve (D7 denied→approved)', async () => {
		const user = userEvent.setup();
		renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');

		// The seeded denied row lives under the Denied filter, without a Deny verb.
		await user.click(screen.getByRole('button', { name: 'Denied' }));
		expect(await screen.findByText('Sketchy Tool')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Deny' })).not.toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('Sketchy Tool approved')).toBeInTheDocument();
		expect(await screen.findByText('No denied clients')).toBeInTheDocument();
	});

	it('has no critical a11y violations on the queue tab', async () => {
		const { container } = renderSection('/settings?tab=queue');
		await screen.findByText('Cursor');
		await checkA11y(container);
	});
});
