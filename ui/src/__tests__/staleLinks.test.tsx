import { beforeEach, describe, expect, it } from 'vitest';
import { useLocation } from 'react-router';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { AuthProvider } from '@/shared/auth/AuthContext';
import { clearToken, setToken } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { App } from '@/App';
import { clearApprovalsStore, seedMockApproval } from '@/modules/agents/mocks/approvalsHandlers';

/**
 * Review and Inbox links whose path is the shell home (`/`) or no page at all
 * land on Agents in the full app, query kept, and open what they name. The
 * redirect runs before the shell and in one hop, so the shell never renders
 * at `/`, where the rail and the review host would act on the query.
 */

/** Every URL (path and query) the app rendered at, in order. */
const visited: string[] = [];
const pathsVisited = () => visited.map((url) => new URL(url, 'http://app.test').pathname);

function LocationProbe() {
	const { pathname, search } = useLocation();
	const url = `${pathname}${search}`;
	if (visited[visited.length - 1] !== url) visited.push(url);
	return <p data-testid="location">{`${pathname}${search}`}</p>;
}

function renderApp(route: string) {
	return renderWithProviders(
		<AuthProvider>
			<App />
			<LocationProbe />
		</AuthProvider>,
		{ route },
	);
}

function location(): URL {
	return new URL(screen.getByTestId('location').textContent ?? '', 'http://app.test');
}

beforeEach(() => {
	visited.length = 0;
	clearToken();
	setToken('mock-access-token');
	clearApprovalsStore();
	seedMockApproval({
		id: 'held_stale',
		agent_id: 'agnt_billing',
		agent_name: 'billing-ops',
		agent_owner_id: 'usr_owner',
		operation_id: 'createCharge',
		created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
		expires_at: new Date(Date.now() + 23 * 3_600_000).toISOString(),
		request: {
			method: 'POST',
			url: 'https://api.stripe.com/v1/charges',
			body: '{"amount":129900}',
			body_truncated: false,
		},
	});
	return () => clearToken();
});

describe('stale review and Inbox links', () => {
	it.each(['/?review=held_stale', '/no-such-page?review=held_stale'])(
		'%s lands on Agents with the review open',
		async (route) => {
			renderApp(route);
			expect(await screen.findByRole('dialog', { name: 'Review call' })).toBeInTheDocument();
			expect(location().pathname).toBe(ROUTES.agents);
			expect(location().searchParams.get('review')).toBe('held_stale');
			expect(pathsVisited().slice(1)).not.toContain('/');
		},
	);

	it.each(['/?inbox=1', '/no-such-page?inbox=1'])(
		'%s lands on Agents with the Inbox open',
		async (route) => {
			renderApp(route);
			await waitFor(() =>
				expect(screen.getByRole('tab', { name: /^Inbox/ })).toHaveAttribute(
					'aria-selected',
					'true',
				),
			);
			await waitFor(() => expect(location().searchParams.has('inbox')).toBe(false));
			expect(location().pathname).toBe(ROUTES.agents);
			expect(await screen.findByRole('heading', { name: 'Agents' })).toBeInTheDocument();
			expect(pathsVisited().slice(1)).not.toContain('/');
			// The redirect carried the param to Agents, and the rail consumed it
			// there: never at `/`, where its strip would undo the redirect.
			expect(visited).toContain('/agents?inbox=1');
		},
	);
});
