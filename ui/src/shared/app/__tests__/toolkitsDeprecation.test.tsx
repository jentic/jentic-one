import { beforeEach, describe, expect, it } from 'vitest';
import { useLocation, useRoutes } from 'react-router';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { ROUTES } from '@/shared/app/routes';
import {
	resetToolkitsDeprecationNotice,
	toolkitsDeprecationRoutes,
} from '@/shared/app/toolkitsDeprecation';
import { clearAllToasts, Toaster } from '@/shared/ui';

/**
 * Deprecation-window alias for the retired Toolkits module (theme-5 5d) —
 * DELETE IN 6b together with `toolkitsDeprecation.tsx`.
 *
 * Router-level: stale `/toolkits` and `/toolkits/{tk_id}` deep links (± the
 * toolkit-era `?tab=` variants) must land on the Agents page — the binding
 * management home — with a one-time retirement notice, instead of falling
 * through to the not-found catch-all.
 */

/** Mirrors the router location into the DOM for post-redirect assertions. */
function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

/**
 * The alias routes exactly as `App.tsx` mounts them, plus a stub claiming the
 * Agents destination (the real page's data needs are not under test here).
 */
function Harness() {
	return useRoutes([
		...toolkitsDeprecationRoutes,
		{ path: ROUTES.agents.slice(1), element: <h1>Agents stub</h1> },
	]);
}

function renderAt(route: string) {
	return renderWithProviders(
		<>
			<Harness />
			<LocationProbe />
			<Toaster />
		</>,
		{ route },
	);
}

describe('toolkits deprecation alias (theme-5 5d — delete in 6b)', () => {
	beforeEach(() => {
		resetToolkitsDeprecationNotice();
		clearAllToasts();
	});

	it('redirects /toolkits to the Agents page and shows the retirement notice', async () => {
		renderAt('/toolkits?tab=members');

		expect(await screen.findByRole('heading', { name: 'Agents stub' })).toBeInTheDocument();
		// `replace` navigation + the query string dropped: the toolkit-era
		// `?tab=` vocabulary means nothing on the destination.
		expect(screen.getByTestId('location').textContent).toBe(ROUTES.agents);

		// The notice points operators at the replacement surface.
		expect(await screen.findByText('Toolkits were retired')).toBeInTheDocument();
		expect(screen.getByText(/managed per agent/)).toBeInTheDocument();
		expect(screen.getByText(/Access tab/)).toBeInTheDocument();
	});

	it('redirects a /toolkits/{tk_id} detail deep link without resolving the id', async () => {
		// The toolkit endpoints were deleted in 5b — the id is not looked up,
		// the link just lands on the replacement home with the same notice.
		renderAt('/toolkits/tk_0123456789abcdef?tab=agents');

		expect(await screen.findByRole('heading', { name: 'Agents stub' })).toBeInTheDocument();
		expect(screen.getByTestId('location').textContent).toBe(ROUTES.agents);
		expect(await screen.findByText('Toolkits were retired')).toBeInTheDocument();
	});

	it('shows the notice once per page load, and it is dismissible', async () => {
		const user = userEvent.setup();
		const first = renderAt('/toolkits');
		expect(await screen.findByText('Toolkits were retired')).toBeInTheDocument();

		// Dismissible via the toast's own close affordance.
		await user.click(screen.getByRole('button', { name: 'Dismiss' }));
		await waitFor(() => {
			expect(screen.queryByText('Toolkits were retired')).not.toBeInTheDocument();
		});

		// A second stale-link hit in the same session doesn't resurface it.
		first.unmount();
		renderAt('/toolkits/tk_other');
		expect(await screen.findByRole('heading', { name: 'Agents stub' })).toBeInTheDocument();
		expect(screen.queryByText('Toolkits were retired')).not.toBeInTheDocument();
	});
});
