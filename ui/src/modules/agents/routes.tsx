/**
 * Agents module routes. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/agents`. Registered additively into `@/shared/app/routes.ts`.
 */
import { useEffect } from 'react';
import type { RouteObject } from 'react-router';
import { Navigate } from 'react-router';
import { ROUTES } from '@/shared/app';
import { toast } from '@/shared/ui';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import AgentDetailPage from '@/modules/agents/pages/AgentDetailPage';

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

export const agentsRoutes: RouteObject[] = [
	{ path: 'agents', element: <AgentsPage /> },
	// Declared before `agents/:agentId` so the `service-accounts` segment is
	// never captured as an `agentId`.
	{ path: 'agents/service-accounts/*', element: <RetiredServiceAccountRedirect /> },
	{ path: 'agents/:agentId', element: <AgentDetailPage /> },
];
