/**
 * The agent the page is about, if any — `/agents/:agentId` or
 * `/agents?agent=<id>`.
 *
 * The Activity rail does NOT follow it on its own: the rail's actor filter is
 * one choice the user owns, the same on every page. On an agent's page the
 * rail only offers the shortcut ("Only <agent>") — see `ActivityRailBody`.
 */
import { useMemo } from 'react';
import { useLocation } from 'react-router';

const AGENT_PATH = /^\/agents\/([^/]+)\/?/;
/** Sub-paths under `/agents/` that are not an agent id. */
const NOT_AN_AGENT = new Set(['service-accounts']);

export function routeAgentId(pathname: string, search: string): string | null {
	const m = AGENT_PATH.exec(pathname);
	if (m && !NOT_AN_AGENT.has(m[1])) return decodeURIComponent(m[1]);
	if (pathname === '/agents' || pathname === '/agents/') {
		return new URLSearchParams(search).get('agent');
	}
	return null;
}

export function useRouteAgentId(): string | null {
	const { pathname, search } = useLocation();
	return useMemo(() => routeAgentId(pathname, search), [pathname, search]);
}
