/**
 * Agents module routes. Path is RELATIVE to the `/app` shell, so this mounts at
 * `/app/agents`. Registered additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router-dom';
import AgentsPage from '@/modules/agents/pages/AgentsPage';
import AgentDetailPage from '@/modules/agents/pages/AgentDetailPage';
import ServiceAccountDetailPage from '@/modules/agents/pages/ServiceAccountDetailPage';

export const agentsRoutes: RouteObject[] = [
	{ path: 'agents', element: <AgentsPage /> },
	// More specific SA path is declared before `agents/:agentId` so the
	// `service-accounts` segment is never captured as an `agentId`.
	{ path: 'agents/service-accounts/:serviceAccountId', element: <ServiceAccountDetailPage /> },
	{ path: 'agents/:agentId', element: <AgentDetailPage /> },
];
