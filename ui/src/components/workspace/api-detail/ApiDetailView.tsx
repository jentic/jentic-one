import { useQuery } from '@tanstack/react-query';
import { CredentialsSection } from './CredentialsSection';
import { OperationsSection } from './OperationsSection';
import { OverviewStrip } from './OverviewStrip';
import { SecuritySchemesSection } from './SecuritySchemesSection';
import { ApiDetailSkeleton } from './skeletons';
import { ToolkitsSection } from './ToolkitsSection';
import type { BoundToolkit } from './ToolkitsSection';
import { useApiOperations } from './useApiOperations';
import { WorkflowsSection } from './WorkflowsSection';
import { ApiSummary } from '@/components/discovery/ApiSummary';
import { api } from '@/api/client';

interface ApiDetailViewProps {
	apiId: string;
}

/**
 * Composes the API detail surface: a single description summary,
 * an overview strip, then a vertical stack of self-contained sections
 * (credentials → toolkits → operations → workflows → security schemes).
 *
 * This component is intentionally thin — it owns the cross-section
 * data fetching (api record, credentials, workflows, toolkits, last
 * activity) and threads it into purpose-built section components. The
 * operations widget has its own state machine encapsulated in the
 * `useApiOperations` hook; everything else is presentational.
 */
export function ApiDetailView({ apiId }: ApiDetailViewProps) {
	const {
		data: apiData,
		isLoading: isLoadingApi,
		error: apiError,
	} = useQuery({
		queryKey: ['api', apiId],
		queryFn: () => api.getApi(apiId),
	});

	const { data: credentials = [], isLoading: isLoadingCreds } = useQuery({
		queryKey: ['credentials', apiId],
		queryFn: () => api.listCredentials(apiId),
		select: (d: any) => (Array.isArray(d) ? d : []),
	});

	const { data: workflows, isLoading: isLoadingWorkflows } = useQuery({
		queryKey: ['workflows', apiId],
		queryFn: () => api.listWorkflows(undefined, 'local'),
		select: (d: any) => {
			const list = Array.isArray(d) ? d : (d?.workflows ?? []);
			return list.filter((w: any) => w.involved_apis?.includes(apiId) || w.api_id === apiId);
		},
	});

	const lastActivityQuery = useQuery({
		queryKey: ['api-last-activity', apiId],
		queryFn: () => api.listTraces({ limit: 20 }),
		staleTime: 15_000,
	});
	// The traces endpoint returns recent activity for the whole
	// workspace, so we filter client-side for the first trace whose
	// operation_id mentions this API. Cheap, and keeps the index
	// structure on the backend simpler.
	const lastActivityTs = (() => {
		const traces = (
			lastActivityQuery.data as
				| { traces?: Array<{ operation_id?: string; created_at?: number }> }
				| undefined
		)?.traces;
		const match = traces?.find((t) => t.operation_id?.includes(apiId));
		return match?.created_at ?? null;
	})();

	const { data: toolkits } = useQuery({
		queryKey: ['toolkits'],
		queryFn: () => api.listToolkits(),
		select: (d: any) => (Array.isArray(d) ? d : []),
	});

	// For each toolkit, fetch its credentials and keep only the ones
	// that point at this API. The "default" toolkit is a workspace-
	// wide bucket — credentials live there even if no toolkit binds
	// them — so we surface it whenever this API has credentials at all.
	const { data: boundToolkits = [] } = useQuery<BoundToolkit[]>({
		queryKey: [
			'toolkit-api-bindings',
			apiId,
			toolkits?.map((t: any) => t.id).join(','),
			credentials.length,
		],
		queryFn: async () => {
			if (!toolkits || toolkits.length === 0) return [];
			const results = await Promise.all(
				toolkits.map(async (tk: any) => {
					try {
						if (tk.id === 'default') {
							if (credentials.length === 0) return null;
							return {
								id: tk.id,
								name: tk.name ?? 'Default',
								apiCredentials: credentials.map((c: any) => ({
									id: c.id,
									label: c.label || c.id,
								})),
							};
						}
						const creds = await api.listToolkitCredentials(tk.id);
						const arr = Array.isArray(creds) ? creds : [];
						const forApi = arr.filter((c: any) => c.api_id === apiId);
						if (forApi.length === 0) return null;
						return {
							id: tk.id,
							name: tk.name ?? tk.id,
							apiCredentials: forApi.map((c: any) => ({
								id: c.credential_id ?? c.id,
								label: c.credential_label || c.credential_id || c.id,
							})),
						};
					} catch {
						return null;
					}
				}),
			);
			return results.filter(Boolean) as BoundToolkit[];
		},
		enabled: !!toolkits && toolkits.length > 0,
		staleTime: 60_000,
	});

	const ops = useApiOperations(apiId);

	if (isLoadingApi) return <ApiDetailSkeleton />;
	if (apiError) {
		return (
			<div className="py-12 text-center">
				<p className="text-muted-foreground">Failed to load API details.</p>
			</div>
		);
	}
	if (!apiData) return null;

	const securitySchemes: Record<string, unknown> = apiData.security_schemes ?? {};
	const servers: Array<{ url: string }> = apiData.servers ?? [];
	const createdAt = apiData.created_at as number | undefined;
	const description = (apiData.description || apiData.info?.description) as string | undefined;
	const apiTitle = (apiData.name || apiData.info?.title) as string | undefined;
	// Prefer the authoritative count from the API record, falling back
	// to whatever the operations endpoint has reported so far.
	const operationCount = apiData.operation_count ?? ops.opsTotal;

	return (
		<div className="space-y-8">
			<ApiSummary
				description={description}
				title={apiTitle}
				host={servers[0]?.url}
				opCount={operationCount}
				tagCount={ops.tagOptions.length}
			/>

			<OverviewStrip
				servers={servers}
				credentialsCount={credentials.length}
				toolkitsCount={boundToolkits.length}
				operationsCount={operationCount}
				workflowsCount={workflows?.length ?? 0}
				lastActivityTs={lastActivityTs}
				createdAt={createdAt}
			/>

			<CredentialsSection
				credentials={credentials}
				isLoading={isLoadingCreds}
				apiId={apiId}
			/>

			<ToolkitsSection toolkits={boundToolkits} />

			<OperationsSection
				rows={ops.rows}
				visible={ops.visible}
				loaded={ops.operations.length}
				total={ops.opsTotal}
				tagOptions={ops.tagOptions}
				filter={ops.opsFilter}
				onFilterChange={ops.setOpsFilter}
				activeTag={ops.activeTag}
				onTagChange={ops.setActiveTag}
				expandedOps={ops.expandedOps}
				onToggleExpanded={ops.toggleExpanded}
				isLoading={ops.isLoadingOps}
				hasMore={ops.hasMore}
				isFetchingMore={ops.isFetchingMore}
				onLoadMore={ops.loadMore}
			/>

			<WorkflowsSection workflows={workflows} isLoading={isLoadingWorkflows} />

			<SecuritySchemesSection schemes={securitySchemes} />
		</div>
	);
}
