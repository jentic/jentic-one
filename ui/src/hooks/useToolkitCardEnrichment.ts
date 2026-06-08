import { useQueries } from '@tanstack/react-query';
import { useMemo } from 'react';
import { api } from '@/api/client';

export interface ToolkitEnrichment {
	/** Distinct upstream API ids the toolkit's bound credentials touch. */
	apiIds: string[];
	/** Number of agents granted this toolkit. */
	agentCount: number;
}

const EMPTY: ToolkitEnrichment = { apiIds: [], agentCount: 0 };

/**
 * Enriches toolkit cards with the two cross-cutting facts the `/toolkits`
 * list endpoint doesn't return: which APIs a toolkit touches (from its
 * bound credentials' `api_id`s) and how many agents are granted it.
 *
 * Each toolkit is its own react-query keyed `['toolkit-card-enrichment', id]`,
 * so:
 *   - it dedupes with any other component fetching the same toolkit's
 *     credentials/agents (e.g. WorkspaceView's per-toolkit query),
 *   - it can be invalidated individually (or via the `['toolkit-card-enrichment']`
 *     prefix used by the bind/unbind mutations), and
 *   - a single toolkit's 404/permission error degrades to empty enrichment
 *     without blanking the rest of the list.
 *
 * The `default` toolkit is skipped for the API pile — it implicitly contains
 * every credential, so a pile there would be noise.
 */
export function useToolkitCardEnrichment(toolkitIds: string[]): Map<string, ToolkitEnrichment> {
	const results = useQueries({
		queries: toolkitIds.map((id) => ({
			queryKey: ['toolkit-card-enrichment', id],
			queryFn: async (): Promise<ToolkitEnrichment> => {
				const [creds, agentsRes] = await Promise.all([
					id === 'default'
						? Promise.resolve([])
						: api.listToolkitCredentials(id).catch(() => []),
					api.listToolkitAgents(id).catch(() => ({ agents: [] })),
				]);
				const apiIds = Array.from(
					new Set(
						(Array.isArray(creds) ? creds : [])
							.map((c) =>
								typeof (c as { api_id?: unknown }).api_id === 'string'
									? (c as { api_id: string }).api_id
									: null,
							)
							.filter((v): v is string => Boolean(v)),
					),
				);
				const agentCount = Array.isArray(agentsRes?.agents) ? agentsRes.agents.length : 0;
				return { apiIds, agentCount };
			},
			staleTime: 60_000,
		})),
	});

	return useMemo(() => {
		const map = new Map<string, ToolkitEnrichment>();
		toolkitIds.forEach((id, i) => {
			map.set(id, results[i]?.data ?? EMPTY);
		});
		return map;
		// `results` identity changes each render; depend on the resolved data
		// snapshot instead so the Map is only rebuilt when enrichment changes.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [toolkitIds.join(','), results.map((r) => r.dataUpdatedAt).join(',')]);
}
