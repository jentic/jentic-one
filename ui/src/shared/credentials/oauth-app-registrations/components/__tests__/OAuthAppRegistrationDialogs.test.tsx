import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { SharedOAuthAppsSection } from '@/shared/credentials/oauth-app-registrations/components/SharedOAuthAppsSection';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

/**
 * Edit / Rotate secret / Delete dialogs, driven from the shared-apps section
 * rows against the MSW store. Pins: (a) edit PATCHes the draft and the draft
 * survives an Esc-then-reopen of the same row, (b) rotate posts the new
 * secret and is unavailable for device-flow apps, (c) delete removes an
 * unreferenced app, and a 409 (still referenced) explains why and disables
 * the retry.
 */

type User = ReturnType<typeof userEvent.setup>;

async function openSection(): Promise<User> {
	renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
	const user = userEvent.setup();
	await user.click(screen.getByRole('button', { name: /^shared oauth apps/i }));
	await screen.findByText('GitHub production app');
	return user;
}

describe('OAuth app registration dialogs', () => {
	beforeEach(() => {
		resetOAuthAppRegistrationsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('edit saves the changed fields', async () => {
		let patched: Record<string, unknown> | null = null;
		worker.use(
			http.patch('/oauth-app-registrations/:id', async ({ request, params }) => {
				patched = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: params.id, ...patched });
			}),
		);
		const user = await openSection();

		await user.click(screen.getByRole('button', { name: 'Edit GitHub production app' }));
		const dialog = await screen.findByRole('dialog', { name: /edit github production app/i });
		const name = within(dialog).getByLabelText(/display name/i);
		await user.clear(name);
		await user.type(name, 'GitHub (renamed)');
		await user.click(within(dialog).getByRole('button', { name: /^save$/i }));

		await waitFor(() => expect(patched).not.toBeNull());
		expect(patched).toMatchObject({
			name: 'GitHub (renamed)',
			default_scopes: ['read:user', 'repo'],
			authorize_url: 'https://github.com/login/oauth/authorize',
			token_url: 'https://github.com/login/oauth/access_token',
		});
		// Auth-code apps don't send device-flow endpoints.
		expect(patched).not.toHaveProperty('authorization_endpoint');
		// Activation belongs to the row action, never to Save.
		expect(patched).not.toHaveProperty('is_active');
	});

	it('saving a kept draft does not reactivate an app deactivated since', async () => {
		const user = await openSection();

		// Open Edit, dismiss it — the draft is kept for this app.
		await user.click(screen.getByRole('button', { name: 'Edit GitHub production app' }));
		let dialog = await screen.findByRole('dialog', { name: /edit github production app/i });
		await user.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
		await waitFor(() => expect(dialog).not.toHaveAttribute('open'));

		// Deactivate from the row.
		await user.click(screen.getByRole('button', { name: 'Deactivate GitHub production app' }));
		const confirm = await screen.findByRole('dialog', {
			name: /deactivate github production app\?/i,
		});
		await user.click(within(confirm).getByRole('button', { name: /^deactivate$/i }));
		await screen.findByRole('button', { name: 'Activate GitHub production app' });

		let patched: Record<string, unknown> | null = null;
		worker.use(
			http.patch('/oauth-app-registrations/:id', async ({ request, params }) => {
				patched = (await request.json()) as Record<string, unknown>;
				return HttpResponse.json({ id: params.id, ...patched });
			}),
		);
		await user.click(screen.getByRole('button', { name: 'Edit GitHub production app' }));
		dialog = await screen.findByRole('dialog', { name: /edit github production app/i });
		expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
		await user.type(within(dialog).getByLabelText(/display name/i), ' 2');
		await user.click(within(dialog).getByRole('button', { name: /^save$/i }));

		await waitFor(() => expect(patched).not.toBeNull());
		expect(patched).not.toHaveProperty('is_active');
	});

	it('edit keeps the draft across a dismiss and reopen of the same app', async () => {
		const user = await openSection();

		await user.click(screen.getByRole('button', { name: 'Edit GitHub production app' }));
		let dialog = await screen.findByRole('dialog', { name: /edit github production app/i });
		const name = within(dialog).getByLabelText(/display name/i);
		await user.clear(name);
		await user.type(name, 'Half-typed');
		await user.click(within(dialog).getByRole('button', { name: /^cancel$/i }));
		// The dialog stays mounted (its target persists) but is closed.
		await waitFor(() => expect(dialog).not.toHaveAttribute('open'));

		await user.click(screen.getByRole('button', { name: 'Edit GitHub production app' }));
		dialog = await screen.findByRole('dialog', { name: /edit github production app/i });
		expect(within(dialog).getByLabelText(/display name/i)).toHaveValue('Half-typed');
	});

	it('rotate posts the new secret', async () => {
		let rotated: Record<string, unknown> | null = null;
		worker.use(
			http.post(
				'/oauth-app-registrations/:id\\:rotate-secret',
				async ({ request, params }) => {
					rotated = { id: params.id, ...((await request.json()) as object) };
					return HttpResponse.json({ id: params.id });
				},
			),
		);
		const user = await openSection();

		await user.click(
			screen.getByRole('button', { name: 'Rotate secret for GitHub production app' }),
		);
		const dialog = await screen.findByRole('dialog', { name: /rotate client secret/i });
		await user.type(within(dialog).getByLabelText(/new client secret/i), 'new_s3cret');
		await user.click(within(dialog).getByRole('button', { name: /^rotate$/i }));

		await waitFor(() =>
			expect(rotated).toEqual({ id: 'oar_github_prod', client_secret: 'new_s3cret' }),
		);
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});

	it('rotate is unavailable for device-flow apps', async () => {
		await openSection();
		expect(
			screen.getByRole('button', { name: 'Rotate secret for GitHub CLI (device flow)' }),
		).toBeDisabled();
	});

	it('delete removes an app no credential references', async () => {
		const user = await openSection();

		await user.click(screen.getByRole('button', { name: 'Delete GitHub CLI (device flow)' }));
		const dialog = await screen.findByRole('dialog', {
			name: /delete github cli \(device flow\)\?/i,
		});
		await user.click(within(dialog).getByRole('button', { name: /^delete$/i }));

		await waitFor(() =>
			expect(screen.queryByText('GitHub CLI (device flow)')).not.toBeInTheDocument(),
		);
	});

	it('delete explains a 409 and disables the retry', async () => {
		const user = await openSection();

		await user.click(screen.getByRole('button', { name: 'Delete GitHub production app' }));
		const dialog = await screen.findByRole('dialog', {
			name: /delete github production app\?/i,
		});
		const confirm = within(dialog).getByRole('button', { name: /^delete$/i });
		await user.click(confirm);

		expect(
			await within(dialog).findByText(/still referenced by 2 credentials/i),
		).toBeInTheDocument();
		expect(confirm).toBeDisabled();
		// The "deleting removes…" warning gives way to the in-use explanation.
		expect(within(dialog).queryByText(/deleting removes/i)).not.toBeInTheDocument();
		// The row stays — nothing was deleted.
		expect(screen.getByText('GitHub production app')).toBeInTheDocument();
	});
});
