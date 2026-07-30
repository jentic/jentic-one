import { describe, it, expect, beforeEach } from 'vitest';
import { page } from '@vitest/browser/context';
import { http } from 'msw';
import { worker } from '@/mocks/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	createErrorHandler,
} from '@/__tests__/test-utils';
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

/** The fleet-table `<tr>` containing the given service-account name. */
function tableRowFor(name: string): HTMLElement {
	const inRow = screen.getAllByText(name).find((el) => el.closest('tr'));
	if (!inRow) throw new Error(`No fleet-table row found for "${name}"`);
	return inRow.closest('tr') as HTMLElement;
}

describe('AgentsPage — service accounts', () => {
	beforeEach(async () => {
		// Desktop viewport → table grammar (cards below `sm` are a separate path).
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
	});

	it('lists service accounts', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		// The pending SA renders in both the approval band and the table.
		await screen.findAllByText('nightly-sync');
		expect(screen.getByText('metrics-exporter')).toBeInTheDocument();
	});

	it('creates a service account via the sheet → appears active immediately', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');

		await user.click(screen.getByRole('button', { name: 'New service account' }));
		const sheet = await screen.findByRole('dialog');
		await user.type(within(sheet).getByLabelText('Name'), 'billing-export');
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		// SAs are approved inside the create transaction (unlike agents) → the
		// new row lands in the table already active, never in the queue band.
		expect((await screen.findAllByText('billing-export')).length).toBeGreaterThan(0);
		expect(await screen.findByText('Service account created')).toBeInTheDocument();
	});

	it('sends the picked initial scopes in the create POST body', async () => {
		const user = userEvent.setup();
		// Spy on the POST body, then fall through to the module's stateful mock.
		let postBody: Record<string, unknown> | null = null;
		worker.use(
			http.post('/service-accounts', async ({ request }) => {
				postBody = (await request.clone().json()) as Record<string, unknown>;
				return undefined;
			}),
		);
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');

		await user.click(screen.getByRole('button', { name: 'New service account' }));
		const sheet = await screen.findByRole('dialog');
		await user.type(within(sheet).getByLabelText('Name'), 'scoped-exporter');

		// The optional scopes section is collapsed by default; open it and pick
		// one grantable scope from the catalogue-backed picker.
		await user.click(within(sheet).getByRole('button', { name: /Initial scopes/ }));
		await user.type(await within(sheet).findByLabelText('Search scopes'), 'executions:read');
		await user.click(await within(sheet).findByRole('checkbox', { name: 'executions:read' }));
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		expect(await screen.findByText('Service account created')).toBeInTheDocument();
		expect(postBody).toMatchObject({
			name: 'scoped-exporter',
			scopes: ['executions:read'],
		});
	});

	it('keeps the sheet and draft when the create is rejected (403)', async () => {
		const user = userEvent.setup();
		worker.use(
			createErrorHandler('post', '/service-accounts', {
				status: 403,
				body: { detail: 'forbidden' },
			}),
		);
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');

		await user.click(screen.getByRole('button', { name: 'New service account' }));
		const sheet = await screen.findByRole('dialog');
		await user.type(within(sheet).getByLabelText('Name'), 'forbidden-export');
		await user.click(within(sheet).getByRole('button', { name: 'Create' }));

		// The hook toasts the failure; the sheet stays open with the draft so the
		// operator can retry (nothing was created, so no phantom row either).
		expect(
			await screen.findByText('Failed to create the service account.'),
		).toBeInTheDocument();
		expect(screen.getByRole('dialog')).toBeInTheDocument();
		expect(within(sheet).getByLabelText('Name')).toHaveValue('forbidden-export');
		// Nothing was created → no phantom row in the roster behind the sheet.
		expect(
			screen.queryAllByText('forbidden-export').filter((el) => el.closest('tr')),
		).toHaveLength(0);
	});

	it('approves a pending service account from the queue band', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');

		const queue = screen.getByRole('region', { name: /Awaiting approval/i });
		await user.click(within(queue).getByRole('button', { name: 'Approve nightly-sync' }));

		await waitFor(() => {
			expect(within(tableRowFor('nightly-sync')).getByText('Active')).toBeInTheDocument();
		});
	});

	it('denies a pending service account with a reason', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');

		const queue = screen.getByRole('region', { name: /Awaiting approval/i });
		await user.click(within(queue).getByRole('button', { name: 'Deny nightly-sync' }));

		const dialog = await screen.findByRole('dialog');
		await user.type(within(dialog).getByLabelText('Reason'), 'untrusted');
		await user.click(within(dialog).getByRole('button', { name: 'Deny' }));

		await waitFor(() => {
			expect(within(tableRowFor('nightly-sync')).getByText('Rejected')).toBeInTheDocument();
		});
	});

	it('disables then re-enables an active service account via the kebab', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findByText('metrics-exporter');

		await user.click(
			within(tableRowFor('metrics-exporter')).getByRole('button', {
				name: 'Actions for metrics-exporter',
			}),
		);
		await user.click(screen.getByRole('menuitem', { name: 'Disable metrics-exporter' }));
		const disableDialog = await screen.findByRole('dialog');
		await user.click(within(disableDialog).getByRole('button', { name: 'Disable' }));

		await waitFor(() => {
			expect(
				within(tableRowFor('metrics-exporter')).getByText('Disabled'),
			).toBeInTheDocument();
		});

		await user.click(
			within(tableRowFor('metrics-exporter')).getByRole('button', {
				name: 'Actions for metrics-exporter',
			}),
		);
		await user.click(screen.getByRole('menuitem', { name: 'Enable metrics-exporter' }));
		await waitFor(() => {
			expect(within(tableRowFor('metrics-exporter')).getByText('Active')).toBeInTheDocument();
		});
	});

	it('announces a service account (not an agent) to assistive tech', async () => {
		const user = userEvent.setup();
		await gotoServiceAccounts(user);
		await screen.findAllByText('nightly-sync');
		// The identity badge labels the actor as a service account (the pending
		// one renders in both the queue band and the table, hence getAll).
		expect(screen.getAllByLabelText('Service account nightly-sync').length).toBeGreaterThan(0);
	});
});
