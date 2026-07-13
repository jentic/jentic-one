import { describe, it, expect, beforeEach } from 'vitest';
import { renderWithProviders, screen, waitFor, within, userEvent } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

async function gotoServiceAccounts(user: ReturnType<typeof userEvent.setup>) {
	renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
	);
	await user.click(await screen.findByRole('button', { name: 'Service accounts' }));
}

describe('AgentsPage — service accounts', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists service accounts', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		expect(await screen.findByText('nightly-sync')).toBeInTheDocument();
		expect(screen.getByText('metrics-exporter')).toBeInTheDocument();
	});

	it('creates a service account via the sheet → appears pending', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('nightly-sync');

		await user.click(screen.getByRole('button', { name: 'New service account' }));
		const sheet = await screen.findByRole('dialog');
		await user.type(within(sheet).getByLabelText('Name'), 'billing-export');
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		expect(await screen.findByText('billing-export')).toBeInTheDocument();
		expect(await screen.findByText('Service account created')).toBeInTheDocument();
	});

	it('approves a pending service account', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('nightly-sync');

		const row = () => screen.getByText('nightly-sync').closest('div.group') as HTMLElement;
		await user.click(within(row()).getByRole('button', { name: 'Approve nightly-sync' }));

		await waitFor(() => {
			expect(within(row()).getByText('Active')).toBeInTheDocument();
		});
	});

	it('denies a pending service account with a reason', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('nightly-sync');

		const row = () => screen.getByText('nightly-sync').closest('div.group') as HTMLElement;
		await user.click(within(row()).getByRole('button', { name: 'Deny nightly-sync' }));

		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Reason'), 'untrusted');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => {
			expect(within(row()).getByText('Rejected')).toBeInTheDocument();
		});
	});

	it('disables then re-enables an active service account', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('metrics-exporter');

		const row = () => screen.getByText('metrics-exporter').closest('div.group') as HTMLElement;
		await user.click(within(row()).getByRole('button', { name: 'Disable metrics-exporter' }));
		const disableDialog = await screen.findByRole('dialog');
		await user.click(within(disableDialog).getByRole('button', { name: 'Disable' }));

		await waitFor(() => {
			expect(within(row()).getAllByText('Disabled').length).toBeGreaterThanOrEqual(1);
		});

		await user.click(within(row()).getByRole('button', { name: 'Enable metrics-exporter' }));
		await waitFor(() => {
			expect(within(row()).getByText('Active')).toBeInTheDocument();
		});
	});

	it('announces a service account (not an agent) to assistive tech', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('nightly-sync');
		// The identity badge labels the actor as a service account.
		expect(screen.getByLabelText('Service account nightly-sync')).toBeInTheDocument();
	});
});
