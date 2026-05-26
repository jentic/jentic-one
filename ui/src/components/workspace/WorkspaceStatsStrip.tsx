import { useQuery } from '@tanstack/react-query';
import { Activity, Boxes, KeyRound, Layers, Workflow } from 'lucide-react';
import { api } from '@/api/client';
import { timeAgo } from '@/lib/time';

/**
 * Compact horizontal strip rendered just below the Workspace page
 * header. Four at-a-glance numbers replace the catalog-browser toolbar
 * that lived here previously.
 *
 * Designed to read like a dashboard ribbon — labelled, low-noise, never
 * a clickable nav surface. Each stat fetches its own narrow query (a
 * 1-row probe for `total`, the cheap workflows list, the toolkits list,
 * and the most recent trace) so the strip never blocks the rest of the
 * page from paging in.
 */
export function WorkspaceStatsStrip() {
	const apisCount = useQuery({
		queryKey: ['workspace-stats', 'apis-count'],
		queryFn: () => api.listApis(1, 1, 'local'),
		staleTime: 30_000,
	});
	const apiTotal = (apisCount.data as { total?: number } | undefined)?.total ?? null;

	const workflowsCount = useQuery({
		queryKey: ['workspace-stats', 'workflows'],
		queryFn: () => api.listWorkflows(undefined, 'local'),
		staleTime: 60_000,
	});
	const workflowsTotal = Array.isArray(workflowsCount.data) ? workflowsCount.data.length : null;

	const toolkitsCount = useQuery({
		queryKey: ['workspace-stats', 'toolkits'],
		queryFn: () => api.listToolkits(),
		staleTime: 60_000,
	});
	const toolkitsTotal = Array.isArray(toolkitsCount.data) ? toolkitsCount.data.length : null;

	const credentialsCount = useQuery({
		queryKey: ['workspace-stats', 'credentials'],
		queryFn: () => api.listCredentials(),
		staleTime: 60_000,
	});
	const credentialsTotal = Array.isArray(credentialsCount.data)
		? credentialsCount.data.length
		: null;

	const lastTrace = useQuery({
		queryKey: ['workspace-stats', 'last-trace'],
		queryFn: () => api.listTraces({ limit: 1 }),
		staleTime: 15_000,
	});
	const lastTraceTs = (() => {
		const traces = (lastTrace.data as { traces?: Array<{ created_at?: number }> } | undefined)
			?.traces;
		const first = traces?.[0]?.created_at;
		return typeof first === 'number' ? first : null;
	})();

	return (
		<div
			className="border-border/60 bg-muted/20 text-muted-foreground -mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border px-4 py-3 text-xs"
			data-testid="workspace-stats-strip"
		>
			<Stat
				icon={<Boxes size={13} aria-hidden="true" />}
				label="APIs"
				value={apiTotal}
				loading={apisCount.isLoading}
				testId="workspace-stat-apis"
			/>
			<Stat
				icon={<Workflow size={13} aria-hidden="true" />}
				label="Workflows"
				value={workflowsTotal}
				loading={workflowsCount.isLoading}
				testId="workspace-stat-workflows"
			/>
			<Stat
				icon={<KeyRound size={13} aria-hidden="true" />}
				label="Credentials"
				value={credentialsTotal}
				loading={credentialsCount.isLoading}
				testId="workspace-stat-credentials"
			/>
			<Stat
				icon={<Layers size={13} aria-hidden="true" />}
				label="Toolkits"
				value={toolkitsTotal}
				loading={toolkitsCount.isLoading}
				testId="workspace-stat-toolkits"
			/>
			<Stat
				icon={<Activity size={13} aria-hidden="true" />}
				label="Last activity"
				value={lastTraceTs ? timeAgo(lastTraceTs) : '—'}
				loading={lastTrace.isLoading}
				testId="workspace-stat-activity"
				className="ml-auto"
			/>
		</div>
	);
}

function Stat({
	icon,
	label,
	value,
	loading,
	testId,
	className,
}: {
	icon: React.ReactNode;
	label: string;
	value: number | string | null;
	loading: boolean;
	testId: string;
	className?: string;
}) {
	const display = loading
		? '…'
		: value === null || value === undefined
			? '—'
			: typeof value === 'number'
				? value.toLocaleString()
				: value;
	return (
		<span
			className={`inline-flex items-baseline gap-2${className ? ` ${className}` : ''}`}
			data-testid={testId}
		>
			<span className="text-muted-foreground/70 inline-flex items-center gap-1.5 self-center">
				{icon}
				<span className="text-[10px] tracking-wider uppercase">{label}</span>
			</span>
			<span className="text-foreground font-mono text-sm font-medium">{display}</span>
		</span>
	);
}
