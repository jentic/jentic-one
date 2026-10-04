// Credentials cache-slice query keys — a leaf (no hooks, no barrel imports) so
// every file in this folder, including the ones `./index` re-exports, can build
// the same keys without closing an import cycle.
import type { ListCredentialsParams } from './client';

/** Namespaced query keys for the credentials cache slice. */
export const credentialKeys = {
	all: ['credentials'] as const,
	list: (params: ListCredentialsParams = {}) => ['credentials', 'list', params] as const,
	/** Every page of `useAllCredentials` — its own key (an infinite query
	 * can't share one with a first-page list) but under the same
	 * `['credentials', 'list', …]` prefix so existing invalidations sweep it. */
	listAll: () => ['credentials', 'list', 'all-pages'] as const,
	detail: (id: string) => ['credentials', 'detail', id] as const,
	/**
	 * Agents directly bound to one credential (`GET /credentials/{id}/agents`,
	 * theme 5 phase 1's reverse lookup). The agents module's bind / unbind /
	 * resume mutations invalidate this slice (importing this factory — the
	 * sanctioned shared channel) so the credential-side "Bound agents" view
	 * never shows a binding the agent side just changed.
	 */
	agents: (id: string) => ['credentials', 'agents', id] as const,
	/** Every page of `useAllCredentialAgents` — own key for the same reason
	 * as {@link credentialKeys.listAll}, under the same prefix so the agents
	 * module's bind/unbind invalidations sweep it. */
	agentsAll: (id: string) => ['credentials', 'agents', id, 'all-pages'] as const,
};
