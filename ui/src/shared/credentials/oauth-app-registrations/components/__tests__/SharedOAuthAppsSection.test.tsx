import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { SharedOAuthAppsSection } from '@/shared/credentials/oauth-app-registrations/components/SharedOAuthAppsSection';
import { resetOAuthAppRegistrationsStore } from '@/shared/credentials/oauth-app-registrations/mocks/handlers';

/**
 * The admin's shared-OAuth-apps management section on the credentials
 * page. Not itself creation-capable — creates route through the credentials
 * Add dialog with the "Available to everyone in the org" toggle flipped.
 * These tests pin: (a) the section renders the seeded registrations,
 * (b) the empty state fires the enclosing page's ``onAddCredential``
 * callback rather than any legacy "go to credentials" nav.
 */

describe('SharedOAuthAppsSection', () => {
	beforeEach(() => {
		resetOAuthAppRegistrationsStore();
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it('renders seeded registrations grouped under the section heading', async () => {
		renderWithProviders(<SharedOAuthAppsSection onAddCredential={vi.fn()} />);
		// Heading is rendered synchronously (no query gates it).
		expect(screen.getByRole('heading', { name: /shared oauth apps/i })).toBeInTheDocument();
		// The three seeded rows land in the table once the list query resolves.
		expect(await screen.findByText('GitHub production app')).toBeInTheDocument();
		expect(await screen.findByText('GitHub CLI (device flow)')).toBeInTheDocument();
		expect(await screen.findByText('Slack (paused)')).toBeInTheDocument();
	});

	it('empty state fires onAddCredential — no legacy "go to credentials" nav', async () => {
		// Override the seeded list with an empty payload so the EmptyState branch renders.
		worker.use(http.get('/oauth-app-registrations', () => HttpResponse.json({ data: [] })));

		const onAddCredential = vi.fn();
		renderWithProviders(<SharedOAuthAppsSection onAddCredential={onAddCredential} />);

		// EmptyState "Add credential" CTA supersedes the pre-refactor "Go to credentials" link.
		const addBtn = await screen.findByRole('button', { name: /add credential/i });
		expect(screen.queryByRole('link', { name: /go to credentials/i })).not.toBeInTheDocument();

		const user = userEvent.setup();
		await user.click(addBtn);
		await waitFor(() => expect(onAddCredential).toHaveBeenCalledTimes(1));
	});
});
