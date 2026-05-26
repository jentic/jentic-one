import { useState, useMemo, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Key, KeyRound, Layers, ChevronDown, Shield } from 'lucide-react';
import { api } from '@/api/client';
import { MethodBadge } from '@/components/ui/Badge';
import { AppLink } from '@/components/ui/AppLink';
import { Skeleton } from '@/components/ui/Skeleton';
import { WorkflowRow } from '@/components/ui/WorkflowRow';
import { ApiSummary } from '@/components/discovery/ApiSummary';
import { SectionTitle } from '@/components/discovery/SectionTitle';
import {
	OPS_PAGE_SIZE,
	topTags,
	filterOps,
	OperationsListToolbar,
	OperationsListFooter,
} from '@/components/discovery/OperationsListControls';
import type { OpRow } from '@/components/discovery/OperationsListControls';
import {
	OperationInspectContent,
	flattenInspectParameters,
	normalizeWorkspaceAuth,
} from '@/components/discovery/OperationInspect';
import type { InspectParam, InspectAuthEntry } from '@/components/discovery/OperationInspect';
import { parseCapabilityId } from '@/lib/capabilityId';
import { timeAgo } from '@/lib/time';

interface ApiDetailViewProps {
	apiId: string;
}

export function ApiDetailView({ apiId }: ApiDetailViewProps) {
	const [opsFilter, setOpsFilter] = useState('');
	const [activeTag, setActiveTag] = useState<string | null>(null);
	const [expandedOps, setExpandedOps] = useState<Set<string>>(new Set());
	const [pages, setPages] = useState(1);

	const prevApiIdRef = useRef(apiId);
	const accumulatorRef = useRef<any[]>([]);
	const seenKeysRef = useRef<Set<string>>(new Set());

	if (prevApiIdRef.current !== apiId) {
		prevApiIdRef.current = apiId;
		accumulatorRef.current = [];
		seenKeysRef.current = new Set();
		setPages(1);
		setOpsFilter('');
		setActiveTag(null);
		setExpandedOps(new Set());
	}

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

	const offset = (pages - 1) * OPS_PAGE_SIZE;

	const {
		data: opsData,
		isLoading: isLoadingOps,
		isFetching,
	} = useQuery({
		queryKey: ['operations', apiId, offset],
		queryFn: () => api.listOperations(apiId, 1, OPS_PAGE_SIZE, { offset }),
		staleTime: 5 * 60_000,
	});

	// When user types a filter and we haven't loaded all ops yet, fetch the rest
	const prelimTotal = opsData?.total ?? 0;
	const needsAll = (opsFilter.trim() !== '' || activeTag !== null) && prelimTotal > 0;
	const { data: allOpsData } = useQuery({
		queryKey: ['operations-all', apiId],
		queryFn: async () => {
			const all: any[] = [];
			let off = 0;
			const batchSize = 200;
			while (true) {
				const batch = await api.listOperations(apiId, 1, batchSize, { offset: off });
				all.push(...(batch.data ?? []));
				if (all.length >= (batch.total ?? 0) || (batch.data?.length ?? 0) < batchSize)
					break;
				off += batchSize;
			}
			return { data: all, total: all.length };
		},
		staleTime: 5 * 60_000,
		enabled: needsAll,
	});

	const { data: toolkits } = useQuery({
		queryKey: ['toolkits'],
		queryFn: () => api.listToolkits(),
		select: (d: any) => (Array.isArray(d) ? d : []),
	});

	const { data: boundToolkits = [] } = useQuery<
		Array<{ id: string; name: string; apiCredentials: Array<{ id: string; label: string }> }>
	>({
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
			return results.filter(Boolean) as Array<{
				id: string;
				name: string;
				apiCredentials: Array<{ id: string; label: string }>;
			}>;
		},
		enabled: !!toolkits && toolkits.length > 0,
		staleTime: 60_000,
	});

	// Accumulate operations across pages (same pattern as SheetBody)
	const operations = useMemo(() => {
		// When filtering and all ops are available, use the full set directly
		if (needsAll && allOpsData?.data?.length) {
			return allOpsData.data;
		}
		const incoming = opsData?.data ?? [];
		if (incoming.length === 0) return accumulatorRef.current;
		let changed = false;
		const next = [...accumulatorRef.current];
		for (const op of incoming) {
			const key = op.id;
			if (seenKeysRef.current.has(key)) continue;
			seenKeysRef.current.add(key);
			next.push(op);
			changed = true;
		}
		if (changed) {
			accumulatorRef.current = next;
		}
		return accumulatorRef.current;
	}, [opsData, needsAll, allOpsData]);

	const opsTotal = opsData?.total ?? operations.length;
	const hasMore = !needsAll && operations.length < opsTotal;
	const isFetchingMore = isFetching && pages > 1;

	const rows: OpRow[] = useMemo(
		() =>
			operations.map((op: any) => {
				const parsed = parseCapabilityId(op.id ?? '');
				return {
					key:
						op.id ??
						`${parsed?.method ?? ''} ${parsed?.host ?? ''}${parsed?.path ?? ''}`,
					method: parsed?.method ?? op.method,
					path: parsed?.path ?? op.path,
					label: op.summary || op.id || '',
					tags: Array.isArray(op.tags) ? op.tags : [],
				};
			}),
		[operations],
	);

	const tagOptions = useMemo(() => topTags(rows.flatMap((r) => r.tags)), [rows]);
	const visible = useMemo(
		() => filterOps(rows, opsFilter, activeTag),
		[rows, opsFilter, activeTag],
	);

	if (isLoadingApi) return <ApiDetailSkeleton />;
	if (apiError) {
		return (
			<div className="py-12 text-center">
				<p className="text-muted-foreground">Failed to load API details.</p>
			</div>
		);
	}
	if (!apiData) return null;

	const securitySchemes: Record<string, any> = apiData.security_schemes ?? {};
	const servers: Array<{ url: string }> = apiData.servers ?? [];
	const createdAt = apiData.created_at as number | undefined;
	const description = (apiData.description || apiData.info?.description) as string | undefined;
	const apiTitle = (apiData.name || apiData.info?.title) as string | undefined;

	return (
		<div className="space-y-8">
			{/* Description */}
			<ApiSummary
				description={description}
				title={apiTitle}
				host={servers[0]?.url}
				opCount={apiData.operation_count ?? opsTotal}
				tagCount={tagOptions.length}
			/>

			{/* Overview */}
			<section className="border-border/50 rounded-lg border">
				{/* Servers */}
				{servers.length > 0 && (
					<div className="border-border/30 border-b px-4 py-3">
						<p className="text-muted-foreground mb-1.5 text-[11px] font-medium tracking-wide uppercase">
							Server{servers.length > 1 ? 's' : ''}
						</p>
						<div className="space-y-1">
							{servers.map((s, i) => (
								<code
									key={i}
									className="text-foreground block truncate font-mono text-xs"
								>
									{s.url}
								</code>
							))}
						</div>
					</div>
				)}
				{/* Stats row */}
				<div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
					<StatItem label="Credentials" value={credentials.length} />
					<StatItem label="Toolkits" value={boundToolkits.length} />
					<StatItem label="Operations" value={apiData.operation_count ?? opsTotal} />
					<StatItem label="Workflows" value={workflows?.length ?? 0} />
					{createdAt && (
						<span className="text-muted-foreground ml-auto text-xs">
							Imported{' '}
							<time dateTime={new Date(createdAt * 1000).toISOString()}>
								{timeAgo(createdAt)}
							</time>
						</span>
					)}
				</div>
			</section>

			{/* Credentials */}
			<CredentialsSection
				credentials={credentials}
				isLoading={isLoadingCreds}
				apiId={apiId}
			/>

			{/* Toolkits */}
			<section>
				<SectionTitle count={boundToolkits.length}>Toolkits</SectionTitle>
				{boundToolkits.length > 0 ? (
					<ul className="mt-3 space-y-3">
						{boundToolkits.map((tk) => (
							<li key={tk.id}>
								<AppLink
									href={`/toolkits/${tk.id}`}
									className="border-border/50 hover:border-primary/40 hover:bg-muted/50 block rounded-lg border p-3 transition-colors"
								>
									<div className="flex items-center gap-2">
										<Layers className="text-muted-foreground h-4 w-4 shrink-0" />
										<span className="text-foreground text-sm font-medium">
											{tk.name}
										</span>
										<span className="text-muted-foreground ml-auto text-xs">
											{tk.apiCredentials.length} credential
											{tk.apiCredentials.length !== 1 ? 's' : ''}
										</span>
									</div>
									{tk.apiCredentials.length > 0 && (
										<div className="mt-2 flex flex-wrap gap-1.5 pl-6">
											{tk.apiCredentials.map((cred) => (
												<span
													key={cred.id}
													className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs"
												>
													<KeyRound className="h-3 w-3" />
													{cred.label}
												</span>
											))}
										</div>
									)}
								</AppLink>
							</li>
						))}
					</ul>
				) : (
					<p className="text-muted-foreground mt-2 text-sm">
						No toolkits bound to this API yet.
					</p>
				)}
			</section>

			{/* Operations */}
			<section>
				<SectionTitle count={opsTotal}>Operations</SectionTitle>

				{isLoadingOps && rows.length === 0 ? (
					<OperationsSkeleton />
				) : rows.length === 0 ? (
					<p className="text-muted-foreground mt-3 text-sm">No operations found.</p>
				) : (
					<div className="mt-3">
						<OperationsListToolbar
							filter={opsFilter}
							onFilterChange={setOpsFilter}
							tags={tagOptions}
							activeTag={activeTag}
							onTagChange={setActiveTag}
							totalOps={opsTotal}
						/>

						{visible.length === 0 ? (
							<p className="text-muted-foreground py-3 text-sm">
								No operations match the current filter.
							</p>
						) : (
							<ul className="divide-border/40 -mx-2 divide-y" data-testid="ops-list">
								{visible.map((row) => (
									<OperationRow
										key={row.key}
										row={row}
										expanded={expandedOps.has(row.key)}
										onToggle={() =>
											setExpandedOps((prev) => {
												const next = new Set(prev);
												if (next.has(row.key)) next.delete(row.key);
												else next.add(row.key);
												return next;
											})
										}
									/>
								))}
							</ul>
						)}

						<OperationsListFooter
							visible={visible.length}
							loaded={operations.length}
							total={opsTotal}
							hasMore={hasMore}
							isFetchingMore={isFetchingMore}
							onLoadMore={() => setPages((p) => p + 1)}
						/>
					</div>
				)}
			</section>

			{/* Workflows */}
			<WorkflowsSection workflows={workflows} isLoading={isLoadingWorkflows} />

			{/* Security schemes */}
			{Object.keys(securitySchemes).length > 0 && (
				<section>
					<SectionTitle count={Object.keys(securitySchemes).length}>
						Security Schemes
					</SectionTitle>
					<ul className="mt-3 space-y-2">
						{Object.entries(securitySchemes).map(([name, scheme]: [string, any]) => (
							<SecuritySchemeCard key={name} name={name} scheme={scheme} />
						))}
					</ul>
				</section>
			)}
		</div>
	);
}

// ── Operation row with inline inspect ──────────────────────────────────────────

function OperationRow({
	row,
	expanded,
	onToggle,
}: {
	row: OpRow;
	expanded: boolean;
	onToggle: () => void;
}) {
	const { data: detail } = useQuery({
		queryKey: ['inspect', row.key],
		queryFn: () => api.inspectCapability(row.key),
		enabled: expanded,
		staleTime: 10 * 60_000,
	});

	const params: InspectParam[] = useMemo(() => {
		if (!detail) return [];
		return flattenInspectParameters(
			(detail as { parameters?: Parameters<typeof flattenInspectParameters>[0] }).parameters,
		);
	}, [detail]);

	const auth: InspectAuthEntry[] = useMemo(() => {
		if (!detail) return [];
		return normalizeWorkspaceAuth(
			(detail as { auth?: Parameters<typeof normalizeWorkspaceAuth>[0] }).auth,
		);
	}, [detail]);

	return (
		<li>
			<button
				type="button"
				onClick={onToggle}
				className="hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors"
				aria-expanded={expanded}
			>
				<MethodBadge method={row.method} />
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate text-sm font-medium">{row.label}</p>
					<code className="text-muted-foreground block truncate font-mono text-xs">
						{row.path}
					</code>
				</div>
				<ChevronDown
					size={14}
					className={`text-muted-foreground mt-1 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
				/>
			</button>
			{expanded && (
				<div className="border-border/30 mb-2 ml-2 border-l-2 pl-4">
					<OperationInspectContent
						description={
							(detail as any)?.description !== (detail as any)?.summary
								? (detail as any)?.description
								: undefined
						}
						parameters={params}
						auth={auth}
					/>
				</div>
			)}
		</li>
	);
}

// ── Credentials section ────────────────────────────────────────────────────────

function CredentialsSection({
	credentials,
	isLoading,
	apiId,
}: {
	credentials: any[];
	isLoading: boolean;
	apiId: string;
}) {
	return (
		<section>
			<SectionTitle count={credentials.length}>Credentials</SectionTitle>
			<div className="mt-3">
				{isLoading ? (
					<div className="space-y-2">
						<Skeleton className="h-12 w-full rounded-lg" />
						<Skeleton className="h-12 w-full rounded-lg" />
					</div>
				) : credentials.length === 0 ? (
					<div className="border-border/50 rounded-lg border border-dashed px-5 py-6 text-center">
						<Key className="text-muted-foreground/50 mx-auto h-6 w-6" />
						<p className="text-muted-foreground mt-2 text-sm">
							No credentials configured yet.
						</p>
						<AppLink
							href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
							className="text-primary hover:text-primary/80 mt-2 inline-flex items-center gap-1 text-sm font-medium"
						>
							<Plus className="h-3.5 w-3.5" /> Add credential
						</AppLink>
					</div>
				) : (
					<ul className="space-y-2">
						{credentials.map((cred: any) => (
							<li key={cred.id}>
								<AppLink
									href={`/credentials/${encodeURIComponent(cred.id)}/edit`}
									className="border-border/50 hover:border-primary/40 hover:bg-muted/50 flex items-center gap-3 rounded-lg border p-3 transition-colors"
								>
									<KeyRound className="text-muted-foreground h-4 w-4 shrink-0" />
									<div className="min-w-0 flex-1">
										<div className="flex items-baseline gap-2">
											<span className="text-foreground text-sm font-medium">
												{cred.label || 'Unnamed'}
											</span>
											{cred.auth_type && (
												<span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium uppercase">
													{cred.auth_type}
												</span>
											)}
										</div>
										{cred.identity && (
											<p className="text-muted-foreground mt-0.5 truncate text-xs">
												{cred.identity}
											</p>
										)}
									</div>
									<span className="text-muted-foreground/60 text-xs whitespace-nowrap">
										{cred.created_at ? timeAgo(cred.created_at) : ''}
									</span>
								</AppLink>
							</li>
						))}
						<li>
							<AppLink
								href={`/credentials/new?api_id=${encodeURIComponent(apiId)}`}
								className="text-primary hover:text-primary/80 mt-1 inline-flex items-center gap-1 text-sm font-medium"
							>
								<Plus className="h-3.5 w-3.5" /> Add credential
							</AppLink>
						</li>
					</ul>
				)}
			</div>
		</section>
	);
}

// ── Workflows section ──────────────────────────────────────────────────────────

function WorkflowsSection({
	workflows,
	isLoading,
}: {
	workflows: any[] | undefined;
	isLoading: boolean;
}) {
	if (isLoading) {
		return (
			<section>
				<SectionTitle>Workflows</SectionTitle>
				<div className="mt-3 space-y-2">
					<Skeleton className="h-14 w-full rounded-lg" />
					<Skeleton className="h-14 w-full rounded-lg" />
				</div>
			</section>
		);
	}

	if (!workflows || workflows.length === 0) return null;

	return (
		<section>
			<SectionTitle count={workflows.length}>Workflows</SectionTitle>
			<ul className="divide-border/40 -mx-2 mt-3 divide-y">
				{workflows.map((wf: any) => (
					<li key={wf.slug}>
						<WorkflowRow
							name={wf.name || wf.slug}
							description={wf.description}
							stepsCount={
								Array.isArray(wf.steps) ? wf.steps.length : (wf.steps_count ?? null)
							}
							href={`/workspace/workflows/${wf.slug}`}
						/>
					</li>
				))}
			</ul>
		</section>
	);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function StatItem({ label, value }: { label: string; value: number }) {
	return (
		<div className="flex items-baseline gap-1.5">
			<span className="text-foreground text-sm font-semibold tabular-nums">{value}</span>
			<span className="text-muted-foreground text-xs">{label}</span>
		</div>
	);
}

// ── Security scheme card ───────────────────────────────────────────────────────

function formatSchemeType(scheme: Record<string, unknown>): string {
	const type = scheme.type as string | undefined;
	if (!type) return 'Unknown';

	switch (type) {
		case 'http': {
			const httpScheme = (scheme.scheme as string | undefined)?.toLowerCase();
			if (httpScheme === 'bearer') {
				const format = scheme.bearerFormat as string | undefined;
				return format ? `Bearer (${format})` : 'Bearer Token';
			}
			if (httpScheme === 'basic') return 'Basic Auth';
			return `HTTP ${httpScheme ?? ''}`.trim();
		}
		case 'apiKey': {
			const loc = scheme.in as string | undefined;
			const name = scheme.name as string | undefined;
			if (loc && name) return `API Key in ${loc} (${name})`;
			if (loc) return `API Key in ${loc}`;
			return 'API Key';
		}
		case 'oauth2':
			return 'OAuth 2.0';
		case 'openIdConnect':
			return 'OpenID Connect';
		default:
			return type;
	}
}

function SecuritySchemeCard({ name, scheme }: { name: string; scheme: Record<string, unknown> }) {
	const label = formatSchemeType(scheme);
	const description = scheme.description as string | undefined;
	const httpScheme = scheme.scheme as string | undefined;
	const bearerFormat = scheme.bearerFormat as string | undefined;
	const keyIn = scheme.in as string | undefined;
	const keyName = scheme.name as string | undefined;
	const openIdUrl = scheme.openIdConnectUrl as string | undefined;

	return (
		<li className="border-border/50 rounded-lg border p-3">
			<div className="flex items-start gap-3">
				<Shield className="text-muted-foreground mt-0.5 h-4 w-4 shrink-0" />
				<div className="min-w-0 flex-1">
					<div className="flex items-baseline gap-2">
						<code className="text-foreground text-sm font-medium">{name}</code>
						<span className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium">
							{label}
						</span>
					</div>
					{description && (
						<p className="text-muted-foreground mt-1 text-xs leading-relaxed">
							{description}
						</p>
					)}
					<div className="text-muted-foreground/80 mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
						{httpScheme && httpScheme !== 'bearer' && httpScheme !== 'basic' && (
							<span>
								Scheme: <code className="text-foreground/70">{httpScheme}</code>
							</span>
						)}
						{bearerFormat && (
							<span>
								Format: <code className="text-foreground/70">{bearerFormat}</code>
							</span>
						)}
						{keyIn && (
							<span>
								Location: <code className="text-foreground/70">{keyIn}</code>
							</span>
						)}
						{keyName && (
							<span>
								Parameter: <code className="text-foreground/70">{keyName}</code>
							</span>
						)}
						{openIdUrl && (
							<span>
								Discovery:{' '}
								<a
									href={openIdUrl}
									target="_blank"
									rel="noopener noreferrer"
									className="text-primary hover:underline"
								>
									{openIdUrl}
								</a>
							</span>
						)}
					</div>
				</div>
			</div>
		</li>
	);
}

// ── Skeletons ──────────────────────────────────────────────────────────────────

function ApiDetailSkeleton() {
	return (
		<div className="space-y-8">
			{/* Overview section */}
			<section className="border-border/50 rounded-lg border">
				<div className="border-border/30 border-b px-4 py-3">
					<Skeleton className="h-3 w-16" />
					<Skeleton className="mt-1.5 h-4 w-56" />
				</div>
				<div className="flex flex-wrap gap-x-6 gap-y-2 px-4 py-3">
					<Skeleton className="h-4 w-20" />
					<Skeleton className="h-4 w-16" />
					<Skeleton className="h-4 w-20" />
					<Skeleton className="h-4 w-20" />
				</div>
			</section>

			{/* Credentials section */}
			<section className="space-y-3">
				<Skeleton className="h-5 w-28" />
				<Skeleton className="h-14 w-full rounded-lg" />
				<Skeleton className="h-14 w-full rounded-lg" />
			</section>

			{/* Toolkits section */}
			<section className="space-y-3">
				<Skeleton className="h-5 w-20" />
				<Skeleton className="h-14 w-full rounded-lg" />
			</section>

			{/* Operations section */}
			<section className="space-y-3">
				<Skeleton className="h-5 w-24" />
				<Skeleton className="h-9 w-64 rounded-lg" />
				<div className="space-y-1">
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
				</div>
			</section>

			{/* Workflows section */}
			<section className="space-y-3">
				<Skeleton className="h-5 w-24" />
				<Skeleton className="h-12 w-full rounded-lg" />
				<Skeleton className="h-12 w-full rounded-lg" />
			</section>
		</div>
	);
}

function OperationsSkeleton() {
	return (
		<div className="mt-3 space-y-2">
			<Skeleton className="h-8 w-64 rounded-lg" />
			{Array.from({ length: 5 }).map((_, i) => (
				<Skeleton key={i} className="h-12 w-full rounded-lg" />
			))}
		</div>
	);
}
