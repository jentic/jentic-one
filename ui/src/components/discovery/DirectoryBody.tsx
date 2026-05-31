import { useMemo, useState, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, Loader2, Plus, Workflow } from 'lucide-react';
import { SectionTitle } from './SectionTitle';
import { ApiSummary } from './ApiSummary';
import { OperationInspectContent } from './OperationInspect';
import type { InspectParam, InspectAuthEntry } from './OperationInspect';
import {
	OPS_PAGE_SIZE,
	topTags,
	filterOps,
	OperationsListToolbar,
	OperationsListFooter,
} from './OperationsListControls';
import type { OpRow } from './OperationsListControls';
import type { DiscoveryEntity } from './DiscoveryCard';
import { MethodBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { AppLink } from '@/components/ui/AppLink';
import { Skeleton } from '@/components/ui/Skeleton';
import { api } from '@/api/client';
import { directoryOpKey, parseDirectoryOpKey } from '@/lib/directoryOpKey';
import { useImportCatalogApi } from '@/hooks/useImportCatalogApi';

export function DirectoryBody({
	apiId,
	initialEntity,
	onSelectOp,
}: {
	apiId: string;
	initialEntity?: DiscoveryEntity;
	onSelectOp: (opId: string) => void;
}) {
	const githubUrl: string | undefined = initialEntity?.raw?._links?.github;
	const specUrl: string | undefined =
		initialEntity?.specUrl ?? initialEntity?.raw?.spec_url ?? undefined;
	const { importApi, pendingApiId } = useImportCatalogApi();
	const isImporting = pendingApiId === apiId;

	const [pages, setPages] = useState<number>(1);
	const [filter, setFilter] = useState('');
	const [activeTag, setActiveTag] = useState<string | null>(null);
	const prevApiIdRef = useRef(apiId);
	const accumulatorRef = useRef<any[]>([]);
	const seenKeysRef = useRef<Set<string>>(new Set());

	if (prevApiIdRef.current !== apiId) {
		prevApiIdRef.current = apiId;
		accumulatorRef.current = [];
		seenKeysRef.current = new Set();
		setPages(1);
		setFilter('');
		setActiveTag(null);
	}

	const offset = (pages - 1) * OPS_PAGE_SIZE;

	const { data, isLoading, error, isFetching } = useQuery({
		queryKey: ['sheet-ops-directory', apiId, offset],
		queryFn: () => api.previewCatalogOperations(apiId, { offset, limit: OPS_PAGE_SIZE }),
		staleTime: 5 * 60_000,
		retry: 1,
	});

	const operations = useMemo(() => {
		const incoming = data?.data ?? [];
		if (incoming.length === 0) return accumulatorRef.current;
		let changed = false;
		const next = [...accumulatorRef.current];
		for (const op of incoming) {
			const key = directoryOpKey(op.method, op.path);
			if (seenKeysRef.current.has(key)) continue;
			seenKeysRef.current.add(key);
			next.push(op);
			changed = true;
		}
		if (changed) {
			accumulatorRef.current = next;
		}
		return accumulatorRef.current;
	}, [data]);

	const total = data?.total ?? operations.length;
	const hasMore = operations.length < total;
	const isFetchingMore = isFetching && pages > 1;
	const specDescription = data?.info?.description ?? initialEntity?.description;

	const rows: OpRow[] = useMemo(
		() =>
			operations.map((op: any) => ({
				key: directoryOpKey(op.method, op.path),
				method: op.method,
				path: op.path,
				label: op.summary || op.operation_id || op.path,
				tags: Array.isArray(op.tags) ? op.tags : [],
			})),
		[operations],
	);

	const tagOptions = useMemo(() => topTags(rows.flatMap((r) => r.tags)), [rows]);
	const visible = useMemo(() => filterOps(rows, filter, activeTag), [rows, filter, activeTag]);

	return (
		<div className="space-y-5 p-5">
			<div className="space-y-3">
				<ApiSummary
					description={specDescription}
					host={apiId}
					opCount={total}
					tagCount={tagOptions.length}
				/>

				<div className="flex flex-wrap items-center gap-2">
					<Button
						onClick={() => {
							if (isImporting) return;
							void importApi({ apiId, specUrl });
						}}
						disabled={isImporting}
						data-testid="sheet-directory-import"
					>
						{isImporting ? (
							<>
								<Loader2 size={14} className="animate-spin" />
								Importing…
							</>
						) : (
							<>
								<Plus size={14} />
								Import to workspace
							</>
						)}
					</Button>
					{githubUrl && (
						<AppLink
							href={githubUrl}
							className="border-border text-muted-foreground hover:text-foreground hover:bg-muted inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium transition-colors"
						>
							<ExternalLink size={14} /> GitHub
						</AppLink>
					)}
				</div>

				<p className="text-muted-foreground/80 text-xs">
					Importing registers the spec locally so operations become browsable. Add
					credentials from Workspace to make them runnable.
				</p>
			</div>

			<DirectoryWorkflowsSection apiId={apiId} />

			<section>
				<SectionTitle count={isLoading && rows.length === 0 ? undefined : total}>
					Operations
				</SectionTitle>

				{isLoading && rows.length === 0 && (
					<div className="text-muted-foreground flex items-center gap-2 py-4 text-sm">
						<Loader2 className="h-4 w-4 animate-spin" /> Fetching spec…
					</div>
				)}

				{error && (
					<p className="text-danger text-sm">
						Failed to fetch the OpenAPI spec.{' '}
						{(error as Error).message ? `(${(error as Error).message})` : null}
					</p>
				)}

				{!isLoading && !error && rows.length === 0 && (
					<p className="text-muted-foreground text-sm">
						No operations found in the spec.
					</p>
				)}

				{rows.length > 0 && (
					<>
						<OperationsListToolbar
							filter={filter}
							onFilterChange={setFilter}
							tags={tagOptions}
							activeTag={activeTag}
							onTagChange={setActiveTag}
							totalOps={total}
						/>

						{visible.length === 0 ? (
							<p className="text-muted-foreground py-3 text-sm">
								No operations match the current filter.
							</p>
						) : (
							<ul
								className="divide-border/40 -mx-2 divide-y"
								data-testid="sheet-ops-list-directory"
							>
								{visible.map((row) => (
									<li key={row.key}>
										<button
											type="button"
											onClick={() => onSelectOp(row.key)}
											className="hover:bg-muted/50 flex w-full items-start gap-3 rounded-md px-2 py-2 text-left transition-colors"
											data-testid="sheet-ops-row-directory"
										>
											<MethodBadge method={row.method} />
											<div className="min-w-0 flex-1">
												<p className="text-foreground truncate text-sm font-medium">
													{row.label}
												</p>
												<code className="text-muted-foreground block truncate font-mono text-xs">
													{row.path}
												</code>
											</div>
										</button>
									</li>
								))}
							</ul>
						)}

						<OperationsListFooter
							visible={visible.length}
							loaded={operations.length}
							total={total}
							hasMore={hasMore}
							isFetchingMore={isFetchingMore}
							onLoadMore={() => setPages((p) => p + 1)}
						/>
					</>
				)}
			</section>
		</div>
	);
}

// ── Workflows-for-this-API section (directory only) ───────────────────────────

function DirectoryWorkflowsSection({ apiId }: { apiId: string }) {
	const { data, isLoading, error, refetch } = useQuery({
		queryKey: ['sheet-workflows-directory', apiId],
		queryFn: () => api.previewCatalogWorkflows(apiId),
		staleTime: 5 * 60_000,
		retry: 1,
	});

	const workflows = data?.data ?? [];

	if (isLoading) return null;
	if (error) {
		return (
			<section>
				<p className="text-muted-foreground text-sm">
					Workflows couldn't be loaded.{' '}
					<button
						type="button"
						onClick={() => refetch()}
						className="text-primary hover:underline"
					>
						Retry
					</button>
				</p>
			</section>
		);
	}
	if (workflows.length === 0) return null;

	return (
		<section data-testid="sheet-workflows-section-directory">
			<SectionTitle count={workflows.length}>Workflows</SectionTitle>
			<p className="text-muted-foreground/80 mb-2 text-xs italic">
				These ship with the public catalog. Add a credential and they'll be imported
				alongside the API spec.
			</p>
			<ul className="divide-border/40 -mx-2 divide-y" data-testid="sheet-wf-list-directory">
				{workflows.map((wf) => {
					const label = wf.summary ?? wf.workflow_id;
					const description = wf.description?.trim() ?? '';
					return (
						<li key={wf.slug}>
							<div
								className="flex w-full items-start gap-3 rounded-md px-2 py-2"
								data-testid="sheet-wf-row-directory"
							>
								<Workflow className="mt-0.5 h-4 w-4 shrink-0 text-teal-400" />
								<div className="min-w-0 flex-1">
									<p className="text-foreground truncate text-sm font-medium">
										{label}
									</p>
									{description && description !== label && (
										<p className="text-muted-foreground line-clamp-2 text-xs leading-relaxed">
											{description}
										</p>
									)}
									<p className="text-muted-foreground/80 mt-0.5 text-[11px]">
										{wf.steps_count} step{wf.steps_count === 1 ? '' : 's'}
									</p>
								</div>
							</div>
						</li>
					);
				})}
			</ul>
			{data?.github_url && (
				<AppLink
					href={data.github_url}
					className="text-muted-foreground hover:text-foreground mt-2 inline-flex items-center gap-1 text-xs"
				>
					<ExternalLink size={11} /> View on GitHub
				</AppLink>
			)}
		</section>
	);
}

// ── Directory inspect panel ───────────────────────────────────────────────────

export function DirectoryInspectPanel({
	apiId,
	opKey,
	onClose,
	specUrl,
	source = 'directory',
	sourceResolving = false,
}: {
	apiId: string;
	opKey: string;
	onClose: () => void;
	specUrl?: string;
	source?: 'workspace' | 'directory';
	sourceResolving?: boolean;
}) {
	const { importApi, pendingApiId } = useImportCatalogApi();
	const isImporting = pendingApiId === apiId;
	const [imported, setImported] = useState(false);
	const isWorkspace = source === 'workspace' || imported;

	const { data, isLoading, error } = useQuery({
		queryKey: ['sheet-ops-directory-inspect', apiId],
		queryFn: () => api.previewCatalogOperations(apiId, { offset: 0, limit: 200 }),
		staleTime: 5 * 60_000,
		retry: 1,
	});

	const parsed = parseDirectoryOpKey(opKey);
	const op = useMemo(() => {
		if (!parsed || !data?.data) return null;
		return (
			data.data.find(
				(o) => o.method.toUpperCase() === parsed.method && o.path === parsed.path,
			) ?? null
		);
	}, [data, parsed]);

	if (isLoading) {
		return <OperationInspectSkeleton />;
	}

	if (error) {
		return (
			<div className="text-danger p-5 text-sm">
				Failed to fetch the OpenAPI spec to inspect this operation.
			</div>
		);
	}

	if (!op) {
		return (
			<div className="text-muted-foreground p-5 text-sm">
				This operation is no longer present in the spec.{' '}
				<button
					type="button"
					onClick={onClose}
					className="text-primary hover:text-primary/80 underline"
				>
					Back to operations
				</button>
			</div>
		);
	}

	const params: InspectParam[] = op.parameters ?? [];
	const schemes = data?.security_schemes ?? {};
	const auth: InspectAuthEntry[] = (op.security ?? []).map((schemeName) => {
		const scheme = schemes[schemeName];
		const subParts: string[] = [];
		if (scheme?.type) subParts.push(scheme.type);
		if (scheme?.scheme) subParts.push(scheme.scheme);
		if (scheme?.in) subParts.push(`in ${scheme.in}`);
		return {
			label: schemeName,
			sub: subParts.length > 0 ? `(${subParts.join(', ')})` : undefined,
			description: scheme?.description,
		};
	});

	return (
		<OperationInspectContent
			method={op.method}
			path={op.path}
			summary={op.summary}
			description={op.description}
			parameters={params}
			auth={auth}
			testId="sheet-directory-inspect"
			footer={
				sourceResolving ? (
					<div className="bg-muted/30 border-border/40 -mx-5 -mb-5 border-t px-5 py-4">
						<Skeleton className="h-9 w-44 rounded-lg" />
						<Skeleton className="mt-2 h-3 w-64" />
					</div>
				) : !isWorkspace ? (
					<div className="bg-muted/30 border-border/40 -mx-5 -mb-5 space-y-2.5 border-t px-5 py-4">
						<div className="flex items-center gap-2">
							<Button
								onClick={async () => {
									if (isImporting) return;
									await importApi({ apiId, specUrl });
									setImported(true);
								}}
								disabled={isImporting}
								data-testid="sheet-directory-inspect-import"
							>
								{isImporting ? (
									<>
										<Loader2 size={14} className="animate-spin" />
										Importing…
									</>
								) : (
									<>
										<Plus size={14} />
										Import to workspace
									</>
								)}
							</Button>
						</div>
						<p className="text-muted-foreground text-xs">
							Import this API to make this operation executable with your credentials.
						</p>
					</div>
				) : undefined
			}
		/>
	);
}

// ── Skeletons ─────────────────────────────────────────────────────────────────

function OperationInspectSkeleton() {
	return (
		<div className="space-y-5 p-5">
			<div className="space-y-2">
				<div className="flex items-center gap-2">
					<Skeleton className="h-5 w-14 rounded" />
					<Skeleton className="h-4 w-48" />
				</div>
				<Skeleton className="h-4 w-2/3" />
				<Skeleton className="h-3 w-full" />
				<Skeleton className="h-3 w-4/5" />
			</div>
			<div className="space-y-2">
				<Skeleton className="h-4 w-24" />
				<div className="border-border/40 overflow-hidden rounded-lg border">
					<div className="bg-muted/30 border-border/40 border-b px-3 py-2">
						<Skeleton className="h-3 w-full" />
					</div>
					{Array.from({ length: 4 }).map((_, i) => (
						<div
							key={i}
							className="border-border/30 flex gap-3 border-b px-3 py-2 last:border-0"
						>
							<Skeleton className="h-3 w-20" />
							<Skeleton className="h-3 w-12" />
							<Skeleton className="h-3 w-10" />
							<Skeleton className="h-3 flex-1" />
						</div>
					))}
				</div>
			</div>
			<div className="space-y-2">
				<Skeleton className="h-4 w-28" />
				<div className="border-border/40 overflow-hidden rounded-lg border">
					<div className="flex gap-3 px-3 py-2">
						<Skeleton className="h-3 w-24" />
						<Skeleton className="h-3 w-20" />
						<Skeleton className="h-3 flex-1" />
					</div>
				</div>
			</div>
			<div className="bg-muted/30 border-border/40 -mx-5 -mb-5 space-y-2.5 border-t px-5 py-4">
				<Skeleton className="h-9 w-44 rounded-lg" />
				<Skeleton className="h-3 w-64" />
			</div>
		</div>
	);
}
