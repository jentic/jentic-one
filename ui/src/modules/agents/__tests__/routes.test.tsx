import { describe, expect, it } from 'vitest';
import { useLocation, useRoutes } from 'react-router';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { Toaster } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { agentsRoutes } from '@/modules/agents/routes';

/**
 * Theme 8 retired the service-account detail page. Stale
 * `/agents/service-accounts/{sva_id}` bookmarks must land on the agents list
 * (where each migrated account's successor agent lives) rather than being
 * captured by `agents/:agentId` as an agent id called "service-accounts".
 */

function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location">{location.pathname}</div>;
}

function Harness() {
	// The real redirect route, plus stubs for the list and detail pages (their
	// data needs are not under test here).
	const redirect = agentsRoutes.find((r) => r.path === 'agents/service-accounts/*');
	return useRoutes([
		redirect!,
		{ path: ROUTES.agents.slice(1), element: <h1>Agents stub</h1> },
		{ path: 'agents/:agentId', element: <h1>Agent detail stub</h1> },
	]);
}

describe('retired service-account routes (theme 8)', () => {
	it('redirects a service-account detail deep link to the agents list', async () => {
		renderWithProviders(
			<>
				<Harness />
				<LocationProbe />
				<Toaster />
			</>,
			{ route: '/agents/service-accounts/sva_0123456789abcdef' },
		);

		expect(await screen.findByRole('heading', { name: 'Agents stub' })).toBeInTheDocument();
		expect(screen.getByTestId('location').textContent).toBe(ROUTES.agents);
		// OQ-6: a toast explains the redirect.
		expect(
			await screen.findByText("Service accounts were retired. They're now agents."),
		).toBeInTheDocument();
	});
});
