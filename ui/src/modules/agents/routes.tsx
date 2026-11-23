/**
 * Agents module routes. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/agents`. Registered additively into `@/shared/app/routes.ts`.
 */
import { useEffect } from 'react';
import type { RouteObject } from 'react-router';
import { Navigate, useLocation, useParams } from 'react-router';
import { ROUTES, ROUTE_PATHS } from '@/shared/app';
import { toast } from '@/shared/ui';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/**
 * `/app/agents/service-accounts/:id` (the retired service-account detail page —
 * theme 8) redirects to the agents list so old bookmarks land somewhere useful
 * instead of rendering `service-accounts` as an agent id. Each migrated
 * account's successor is an ordinary agent listed there. A toast says why, so
 * the redirect doesn't read as a broken link.
 *
 * A component rather than an inline `<Navigate>` element: `@/shared/app/routes`
 * imports this module to build `moduleRoutes`, so reading `ROUTES` at module
 * evaluation would hit the import cycle's TDZ. Deferring it to render time
 * keeps the shared constant without the cycle biting.
 */
function RetiredServiceAccountRedirect() {
	useEffect(() => {
		// Fixed id: the store dedupes on it, so a StrictMode double-mount (or a
		// quick second stale link) shows one toast, not a stack.
		toast({
			id: 'retired-service-accounts',
			title: "Service accounts were retired. They're now agents.",
		});
	}, []);
	return <Navigate to={ROUTES.agents} replace />;
}

/**
 * `/app/access-requests` (the retired access-request queue — theme 7, epic
 * jentic/jentic-one#1374) redirects to Agents, where agent approvals now live,
 * so old bookmarks land somewhere useful instead of a 404. A component for the
 * same TDZ reason as above.
 */
function RetiredAccessRequestsRedirect() {
	return <Navigate to={ROUTES.agents} replace />;
}

/**
 * `/app/credentials` (the retired standalone Credentials page) redirects to the
 * Agents credential inventory, where credentials now live. The query string is
 * kept: approval links (`?approve=`, with a `&poll_token=` on older ones) minted
 * before the backend moved them to `/app/agents` still point here until they
 * expire, and Agents opens the approval wizard from them. A component for the same TDZ reason as above.
 */
function RetiredCredentialsRedirect() {
	const { search } = useLocation();
	const params = new URLSearchParams(search);
	params.set('credentials', '1');
	return <Navigate to={`${ROUTES.agents}?${params}`} replace />;
}

/**
 * `/app/agents/:agentId` addresses an agent by path; the Agents page addresses
 * it as its selection (`?agent=<id>`), so the path redirects there. Links minted
 * outside the SPA (the CLI's approval pointer) and saved bookmarks keep landing
 * on that agent. Any other query param (e.g. a `?tab=`) is dropped: the Agents
 * page has no URL for its dock sheets. A component for the same TDZ reason as
 * above.
 */
function AgentPathRedirect() {
	const { agentId } = useParams<{ agentId: string }>();
	return <Navigate to={agentId ? ROUTE_PATHS.agentTab(agentId) : ROUTES.agents} replace />;
}

/**
 * `/app/agents/approvals/:id` is the path the broker builds into a held call's
 * `review_url`; the review is a pop-up over the Agents page, so the path
 * redirects to it (`?review=<id>`). A component for the same TDZ reason as
 * above.
 */
function HeldCallReviewRedirect() {
	const { id } = useParams<{ id: string }>();
	return <Navigate to={id ? ROUTE_PATHS.approval(id) : ROUTE_PATHS.approvalsInbox()} replace />;
}

/**
 * `/app/agents/approvals` lands on the Agents page with the shell rail open on
 * its Inbox tab, where every held call waiting on the viewer is listed. A
 * component for the same TDZ reason as above.
 */
function ApprovalsInboxRedirect() {
	return <Navigate to={ROUTE_PATHS.approvalsInbox()} replace />;
}

export const agentsRoutes: RouteObject[] = [
	{ path: 'agents', element: <AgentsPage /> },
	{ path: 'access-requests', element: <RetiredAccessRequestsRedirect /> },
	{ path: 'credentials', element: <RetiredCredentialsRedirect /> },
	// Declared before `agents/:agentId` so the `service-accounts` and
	// `approvals` segments are never captured as an `agentId`.
	{ path: 'agents/service-accounts/*', element: <RetiredServiceAccountRedirect /> },
	// Held calls: the inbox, and one call's review (a held call's `review_url`).
	{ path: 'agents/approvals', element: <ApprovalsInboxRedirect /> },
	{ path: 'agents/approvals/:id', element: <HeldCallReviewRedirect /> },
	{ path: 'agents/:agentId', element: <AgentPathRedirect /> },
];
