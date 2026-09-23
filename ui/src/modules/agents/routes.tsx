/**
 * Agents module routes. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/agents`. Registered additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router';
import { Navigate } from 'react-router';
import { ROUTES } from '@/shared/app';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import AgentDetailPage from '@/modules/agents/pages/AgentDetailPage';

/**
 * `/app/agents/service-accounts/:id` (the retired service-account detail page —
 * theme 8) redirects to the agents list so old bookmarks land somewhere useful
 * instead of rendering `service-accounts` as an agent id. Each migrated
 * account's successor is an ordinary agent listed there.
 *
 * A component rather than an inline `<Navigate>` element: `@/shared/app/routes`
 * imports this module to build `moduleRoutes`, so reading `ROUTES` at module
 * evaluation would hit the import cycle's TDZ. Deferring it to render time
 * keeps the shared constant without the cycle biting.
 */
function RetiredServiceAccountRedirect() {
	return <Navigate to={ROUTES.agents} replace />;
}

export const agentsRoutes: RouteObject[] = [
	{ path: 'agents', element: <AgentsPage /> },
	// Declared before `agents/:agentId` so the `service-accounts` segment is
	// never captured as an `agentId`.
	{ path: 'agents/service-accounts/*', element: <RetiredServiceAccountRedirect /> },
	{ path: 'agents/:agentId', element: <AgentDetailPage /> },
];
