/**
 * LLM Proxy module routes. Paths are RELATIVE to the `/app` shell, so these
 * mount at `/app/llm-proxy` (overview) and `/app/llm-proxy/:sessionId`
 * (the session playground). Registered additively into `@/shared/app/routes.ts`.
 */
import type { RouteObject } from 'react-router-dom';
import LlmProxyPage from '@/modules/llm-proxy/pages/LlmProxyPage';
import SessionPage from '@/modules/llm-proxy/pages/SessionPage';

export const llmProxyRoutes: RouteObject[] = [
	{ path: 'llm-proxy', element: <LlmProxyPage /> },
	{ path: 'llm-proxy/:sessionId', element: <SessionPage /> },
];
