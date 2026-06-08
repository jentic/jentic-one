import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';

export interface ToolkitEnrichment {
	/** Distinct upstream API ids the toolkit's bound credentials touch. */
	apiIds: string[];
	/** Number of agents granted this toolkit. */
	agentCount: number;
}

/**
 * Enriches toolkit cards with the two cross-cutting facts the `/toolkits`
 * list endpoint doesn't return: which APIs a toolkit touches (from its
 * bound credentials' `api_id`s) and how many agents are granted it.
 *
 * Both are fanned out in a single batched react-query (mirroring
 * `WorkspaceView`'s `toolkit-credentials` query) so the work is shared,
 * deduped, and cached for 60s rather than firing 2·N requests on every
 * render. The `default` toolkit is skipped for the API pile — it implicitly
 * contains every credential, so a pile there would be noise.
 *
 * Failures per-toolkit degrade gracefully to empty enrichment so a single
 * 404/permission error never blanks the whole list.
 */
export function useToolkitCardEnrichment(toolkitIds: string[]): Map<string, ToolkitEnrichment> {
	const key = [...toolkitIds].sort().join(',');

	const query = useQuery({
		queryKey: ['toolkit-card-enrichment', key],
		queryFn: async () => {
			const entries = await Promise.all(
				toolkitIds.map(async (id) => {
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
					const agentCount = Array.isArray(agentsRes?.agents)
						? agentsRes.agents.length
						: 0;
					return [id, { apiIds, agentCount }] as const;
				}),
			);
			return new Map<string, ToolkitEnrichment>(entries);
		},
		enabled: toolkitIds.length > 0,
		staleTime: 60_000,
	});

	return query.data ?? new Map();
}
