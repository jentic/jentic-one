/**
 * LLM Proxy · Sessions service tier — TanStack Query hooks.
 *
 * Views call these hooks (never the repository or `@/shared/api` directly).
 * The query-key root `['llm-proxy']` is registered in eslint.config.js
 * (MODULE_QUERY_KEY_ROOTS) so it can't collide with another module's cache.
 */
import { useQuery } from '@tanstack/react-query';
import { getSession, listSessions } from '@/modules/llm-proxy/api/client';

export const llmProxyKeys = {
	all: ['llm-proxy'] as const,
	sessions: () => [...llmProxyKeys.all, 'sessions'] as const,
	session: (id: string) => [...llmProxyKeys.all, 'session', id] as const,
};

export function useSessions() {
	return useQuery({
		queryKey: llmProxyKeys.sessions(),
		queryFn: () => listSessions(),
	});
}

export function useSession(id: string | undefined) {
	return useQuery({
		queryKey: llmProxyKeys.session(id ?? ''),
		queryFn: () => getSession(id as string),
		enabled: Boolean(id),
	});
}
