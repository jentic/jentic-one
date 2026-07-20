/**
 * LLM Proxy · Overview (Level 1).
 *
 * Aggregate metric tiles → primary "calls over time" chart → filter bar →
 * Sessions table. Reads the module's `useSessions` hook (mock-backed for now);
 * filtering is applied client-side over the loaded list.
 */
import { useMemo } from 'react';
import { ErrorAlert, PageHeader, PageHelp, PageShell, SkeletonRows } from '@/shared/ui';
import { useSessions } from '@/modules/llm-proxy/api';
import { MetricTiles } from '@/modules/llm-proxy/components/MetricTiles';
import { SessionActivityChart } from '@/modules/llm-proxy/components/SessionActivityChart';
import { SessionsFilterBar } from '@/modules/llm-proxy/components/SessionsFilterBar';
import { SessionsTable } from '@/modules/llm-proxy/components/SessionsTable';
import { useSessionFilters } from '@/modules/llm-proxy/lib/useSessionFilters';

export default function LlmProxyPage() {
	const { data, isLoading, error } = useSessions();
	const { q, status, api, setQuery, setStatus, setApi, reset, active } = useSessionFilters();

	const sessions = useMemo(() => data?.data ?? [], [data]);
	const methods = useMemo(() => data?.methods ?? {}, [data]);

	const statuses = useMemo(() => [...new Set(sessions.map((s) => s.status))].sort(), [sessions]);
	const apis = useMemo(
		() => [...new Set(sessions.flatMap((s) => s.apis_touched))].sort(),
		[sessions],
	);

	const filtered = useMemo(() => {
		const query = q.trim().toLowerCase();
		return sessions.filter((s) => {
			if (query) {
				const haystack = `${s.title} ${s.actor_id}`.toLowerCase();
				if (!haystack.includes(query)) return false;
			}
			if (status !== 'all' && s.status !== status) return false;
			if (api !== 'all' && !s.apis_touched.includes(api)) return false;
			return true;
		});
	}, [sessions, q, status, api]);

	return (
		<PageShell>
			<PageHeader
				title="LLM Proxy"
				subtitle="Agent sessions, tool calls, and governance — what your agents did, and whether policy behaved."
				actions={
					<PageHelp
						title="About LLM Proxy"
						intro="Every agent run flows through the LLM proxy (for reasoning) and the broker (for tool calls). This surface joins those two logs into sessions so you can see what your agents actually did and whether governance allowed or denied each call."
						sections={[
							{
								heading: 'Overview',
								body: 'Aggregate metrics across all captured sessions, a stacked chart of call volume by governance outcome, and a filterable table of every agent run.',
							},
							{
								heading: 'Sessions',
								body: 'Click a row to open the session playground — the agent → subagent tree with each tool call colour-coded by verdict (allow / deny / error).',
							},
						]}
					/>
				}
			/>

			{error ? (
				<ErrorAlert message={error as Error} />
			) : isLoading ? (
				<div className="space-y-6">
					<SkeletonRows rows={5} />
				</div>
			) : (
				<div className="space-y-6">
					<MetricTiles sessions={sessions} />
					<SessionActivityChart sessions={sessions} methods={methods} />
					<div className="space-y-3">
						<SessionsFilterBar
							q={q}
							status={status}
							api={api}
							statuses={statuses}
							apis={apis}
							active={active}
							onQueryChange={setQuery}
							onStatusChange={setStatus}
							onApiChange={setApi}
							onReset={reset}
						/>
						<SessionsTable sessions={filtered} />
					</div>
				</div>
			)}
		</PageShell>
	);
}
