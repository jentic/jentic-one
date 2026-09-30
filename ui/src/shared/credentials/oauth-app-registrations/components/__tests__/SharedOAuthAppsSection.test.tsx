import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { SharedOAuthAppsSection } from '@/shared/credentials/oauth-app-registrations/components/SharedOAuthAppsSection';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

/**
 * The admin's shared-OAuth-apps management section in the credential
 * inventory sheet. Registration itself runs through the credential create
 * flow; the section's "Register shared app" actions hand off to the host via
 * ``onRegister``. These tests pin: (a) the section renders the seeded
 * registrations, (b) both the header action and the empty-state CTA fire
 * ``onRegister``.
 */

describe('SharedOAuthAppsSection', () => {
	beforeEach(() => {
		resetOAuthAppRegistrationsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('renders seeded registrations grouped under the section heading', async () => {
		renderWithProviders(<SharedOAuthAppsSection onRegister={vi.fn()} />);
		// Heading is rendered synchronously (no query gates it).
		expect(screen.getByRole('heading', { name: /shared oauth apps/i })).toBeInTheDocument();
		// The three seeded rows land in the table once the list query resolves.
		expect(await screen.findByText('GitHub production app')).toBeInTheDocument();
		expect(await screen.findByText('GitHub CLI (device flow)')).toBeInTheDocument();
		expect(await screen.findByText('Slack (paused)')).toBeInTheDocument();
	});

	it('header "Register shared app" fires onRegister', async () => {
		const onRegister = vi.fn();
		renderWithProviders(<SharedOAuthAppsSection onRegister={onRegister} />);
		await screen.findByText('GitHub production app');

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

		expect(await screen.findByText(/no shared oauth apps/i)).toBeInTheDocument();
		const buttons = screen.getAllByRole('button', { name: /register shared app/i });
		// Header action + empty-state CTA.
		expect(buttons).toHaveLength(2);

		const user = userEvent.setup();
		await user.click(buttons[1]);
		await waitFor(() => expect(onRegister).toHaveBeenCalledTimes(1));
	});
});
