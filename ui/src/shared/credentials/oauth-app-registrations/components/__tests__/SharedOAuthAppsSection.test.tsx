import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { SharedOAuthAppsSection } from '@/shared/credentials/oauth-app-registrations/components/SharedOAuthAppsSection';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

/**
 * The admin's shared-OAuth-apps management section in the credential
 * inventory sheet. Registration itself runs through the credential create
 * flow; the section's "Register shared app" actions hand off to the host via
 * ``onRegister``. These tests pin: (a) the section starts collapsed and
 * the disclosure reveals the seeded registrations, (b) both the header
 * action and the empty-state CTA fire ``onRegister``, (c) deactivating asks
 * for confirmation while re-activating goes straight through, (d) a settled
 * toggle unlocks its row, and a failed deactivate keeps the confirm open.
 */

async function expandSection(user: ReturnType<typeof userEvent.setup>): Promise<void> {
	await user.click(screen.getByRole('button', { name: /^shared oauth apps/i }));
}

describe('SharedOAuthAppsSection', () => {
	beforeEach(() => {
		resetOAuthAppRegistrationsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('starts collapsed and reveals seeded registrations when expanded', async () => {
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		// Heading is rendered synchronously (no query gates it).
		expect(screen.getByRole('heading', { name: /shared oauth apps/i })).toBeInTheDocument();
		const disclosure = screen.getByRole('button', { name: /^shared oauth apps/i });
		expect(disclosure).toHaveAttribute('aria-expanded', 'false');
		expect(screen.queryByText('GitHub production app')).not.toBeInTheDocument();

		const user = userEvent.setup();
		await expandSection(user);
		expect(disclosure).toHaveAttribute('aria-expanded', 'true');
		// The three seeded rows land in the list once the query resolves.
		expect(await screen.findByText('GitHub production app')).toBeInTheDocument();
		expect(await screen.findByText('GitHub CLI (device flow)')).toBeInTheDocument();
		expect(await screen.findByText('Slack (paused)')).toBeInTheDocument();
	});

	it('header "Register shared app" fires onRegister', async () => {
		const onRegister = vi.fn();
		renderWithProviders(<SharedOAuthAppsSection onRegister={onRegister} />);

		const user = userEvent.setup();
		await user.click(screen.getByRole('button', { name: /register shared app/i }));
		expect(onRegister).toHaveBeenCalledTimes(1);
	});

	it('empty state offers "Register shared app" and fires onRegister', async () => {
		// Override the seeded list with an empty payload so the EmptyState branch renders.
		worker.use(
			http.get('/oauth-app-registrations', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);

		const onRegister = vi.fn();
		renderWithProviders(<SharedOAuthAppsSection onRegister={onRegister} />);
		const user = userEvent.setup();
		await expandSection(user);

		expect(await screen.findByText(/no shared oauth apps/i)).toBeInTheDocument();
		const buttons = screen.getAllByRole('button', { name: /register shared app/i });
		// Header action + empty-state CTA.
		expect(buttons).toHaveLength(2);

		await user.click(buttons[1]);
		await waitFor(() => expect(onRegister).toHaveBeenCalledTimes(1));
	});

	it('deactivating asks for confirmation before cutting the app off', async () => {
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		const user = userEvent.setup();
		await expandSection(user);

		await user.click(
			await screen.findByRole('button', { name: 'Deactivate GitHub production app' }),
		);
		const dialog = await screen.findByRole('dialog', {
			name: /deactivate github production app\?/i,
		});
		await user.click(within(dialog).getByRole('button', { name: /^deactivate$/i }));

		// The row flips to offer re-activation once the PATCH lands.
		expect(
			await screen.findByRole('button', { name: 'Activate GitHub production app' }),
		).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});

	it('re-activating goes straight through without a confirm', async () => {
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		const user = userEvent.setup();
		await expandSection(user);

		await user.click(await screen.findByRole('button', { name: 'Activate Slack (paused)' }));
		expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
		expect(
			await screen.findByRole('button', { name: 'Deactivate Slack (paused)' }),
		).toBeInTheDocument();
	});

	it('unlocks the row once a toggle settles', async () => {
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		const user = userEvent.setup();
		await expandSection(user);

		await user.click(await screen.findByRole('button', { name: 'Activate Slack (paused)' }));
		await screen.findByRole('button', { name: 'Deactivate Slack (paused)' });
		expect(screen.getByRole('button', { name: 'Edit Slack (paused)' })).toBeEnabled();
		expect(screen.getByRole('button', { name: 'Delete Slack (paused)' })).toBeEnabled();
		expect(screen.getByRole('button', { name: 'Deactivate Slack (paused)' })).toBeEnabled();
	});

	it('keeps the deactivate confirm open when the PATCH fails', async () => {
		worker.use(
			http.patch('/oauth-app-registrations/:id', () =>
				HttpResponse.json(
					{ type: 'about:blank', title: 'Internal error', status: 500 },
					{ status: 500 },
				),
			),
		);
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		const user = userEvent.setup();
		await expandSection(user);

		await user.click(
			await screen.findByRole('button', { name: 'Deactivate GitHub production app' }),
		);
		const dialog = await screen.findByRole('dialog', {
			name: /deactivate github production app\?/i,
		});
		const confirm = within(dialog).getByRole('button', { name: /^deactivate$/i });
		await user.click(confirm);

		await waitFor(() => expect(confirm).toBeEnabled());
		expect(
			screen.getByRole('dialog', { name: /deactivate github production app\?/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole('button', { name: 'Deactivate GitHub production app' }),
		).toBeInTheDocument();
	});
});
