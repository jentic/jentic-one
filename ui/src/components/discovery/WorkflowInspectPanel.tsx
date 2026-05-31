import { Loader2, Plus } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SectionTitle } from './SectionTitle';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { api } from '@/api/client';
import { useImportCatalogApi } from '@/hooks/useImportCatalogApi';

export interface WorkflowInspectPanelProps {
	apiId: string;
	workflowId: string;
	onClose: () => void;
	source: 'workspace' | 'directory';
	sourceResolving?: boolean;
}

function extractSlug(workflowId: string): string {
	const match = workflowId.match(/^POST\/[^/]+\/workflows\/(.+)$/);
	return match ? match[1] : workflowId;
}

export function WorkflowInspectPanel({
	apiId,
	workflowId,
	onClose,
	source,
	sourceResolving = false,
}: WorkflowInspectPanelProps) {
	const slug = extractSlug(workflowId);
	const { importApi, pendingApiId } = useImportCatalogApi();
	const isImporting = pendingApiId === apiId;
	const [imported, setImported] = useState(false);
	const isWorkspace = source === 'workspace' || imported;

	const workspaceQuery = useQuery({
		queryKey: ['workflow', slug],
		queryFn: () => api.getWorkflow(slug),
		staleTime: 60_000,
		enabled: source === 'workspace',
	});

	const catalogQuery = useQuery({
		queryKey: ['sheet-workflows-catalog', apiId],
		queryFn: () => api.previewCatalogWorkflows(apiId),
		staleTime: 5 * 60_000,
		enabled: source === 'directory',
	});

	if (source === 'workspace') {
		if (workspaceQuery.isLoading) return <LoadingState />;
		if (workspaceQuery.error || !workspaceQuery.data)
			return <ErrorState message="Failed to load workflow details." />;

		const wf = workspaceQuery.data as {
			name?: string;
			description?: string;
			steps?: Array<{ step_id?: string; description?: string }>;
			involved_apis?: string[];
		};

		return (
			<WorkspaceContent
				name={wf.name ?? slug}
				description={wf.description}
				steps={wf.steps ?? []}
				involvedApis={wf.involved_apis ?? []}
				slug={slug}
			/>
		);
	}

	// source === 'directory'
	if (catalogQuery.isLoading) return <LoadingState />;
	if (catalogQuery.error || !catalogQuery.data)
		return <ErrorState message="Failed to load workflow details." />;

	const workflows = catalogQuery.data.data ?? [];
	const wf = workflows.find((w) => w.slug === slug || w.workflow_id === slug);

	if (!wf) return <ErrorState message="Workflow not found in this API's catalog." />;

	return (
		<DirectoryContent
			summary={wf.summary ?? wf.workflow_id}
			description={wf.description ?? undefined}
			stepsCount={wf.steps_count}
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
									await importApi({ apiId });
									setImported(true);
								}}
								disabled={isImporting}
								data-testid="sheet-wf-inspect-import"
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
							Import this API to access the full workflow details.
						</p>
					</div>
				) : undefined
			}
		/>
	);
}

// ── Sub-components ────────────────────────────────────────────────────────────

function LoadingState() {
	return (
		<div className="space-y-5 p-5">
			<div className="space-y-1.5">
				<Skeleton className="h-3 w-32" />
				<Skeleton className="h-3 w-full" />
				<Skeleton className="h-3 w-4/5" />
				<Skeleton className="h-3 w-3/5" />
			</div>
			<div className="space-y-2">
				<Skeleton className="h-4 w-16" />
				<div className="border-border/40 overflow-hidden rounded-lg border">
					{Array.from({ length: 3 }).map((_, i) => (
						<div
							key={i}
							className="border-border/30 flex items-center gap-3 border-b px-3 py-2.5 last:border-0"
						>
							<Skeleton className="h-5 w-5 rounded-full" />
							<Skeleton className="h-3 w-24" />
							<Skeleton className="h-3 flex-1" />
						</div>
					))}
				</div>
			</div>
			<div className="bg-muted/30 border-border/40 -mx-5 -mb-5 border-t px-5 py-4">
				<Skeleton className="h-9 w-44 rounded-lg" />
				<Skeleton className="mt-2 h-3 w-64" />
			</div>
		</div>
	);
}

function ErrorState({ message }: { message: string }) {
	return <div className="text-danger p-5 text-sm">{message}</div>;
}

function WorkspaceContent({
	name,
	description,
	steps,
	involvedApis,
	slug,
}: {
	name: string;
	description?: string;
	steps: Array<{ step_id?: string; description?: string }>;
	involvedApis: string[];
	slug: string;
}) {
	const showDescription =
		description && description.trim().toLowerCase() !== name.trim().toLowerCase();

	return (
		<div className="space-y-5 p-5" data-testid="workflow-inspect-panel">
			<div className="space-y-1.5">
				<p className="text-foreground text-sm font-medium">{name}</p>
				{showDescription && (
					<p className="text-muted-foreground text-sm leading-relaxed">{description}</p>
				)}
			</div>

			{steps.length > 0 && (
				<section>
					<SectionTitle count={steps.length}>Steps</SectionTitle>
					<div className="border-border/40 divide-border/30 divide-y overflow-hidden rounded-lg border">
						{steps.map((step, idx) => (
							<div
								key={step.step_id ?? idx}
								className="flex items-start gap-3 px-3 py-2 text-xs"
							>
								<span className="text-muted-foreground/70 mt-px w-5 shrink-0 text-right font-mono">
									{idx + 1}
								</span>
								<div className="min-w-0 flex-1">
									{step.step_id && (
										<code className="text-foreground block truncate font-mono">
											{step.step_id}
										</code>
									)}
									{step.description && (
										<p className="text-muted-foreground mt-0.5 leading-relaxed">
											{step.description}
										</p>
									)}
								</div>
							</div>
						))}
					</div>
				</section>
			)}

			{involvedApis.length > 0 && (
				<section>
					<SectionTitle count={involvedApis.length}>Involved APIs</SectionTitle>
					<div className="flex min-w-0 flex-wrap gap-1.5">
						{involvedApis.map((a) => (
							<span
								key={a}
								className="bg-muted text-foreground max-w-full truncate rounded-md px-2 py-0.5 font-mono text-xs"
							>
								{a}
							</span>
						))}
					</div>
				</section>
			)}

			<div className="border-border/40 border-t pt-4">
				<AppLink
					href={`/workspace/workflows/${encodeURIComponent(slug)}`}
					className="text-primary hover:text-primary/80 text-sm font-medium"
				>
					Open full workflow →
				</AppLink>
			</div>
		</div>
	);
}

function DirectoryContent({
	summary,
	description,
	stepsCount,
	footer,
}: {
	summary: string;
	description?: string;
	stepsCount: number;
	footer?: ReactNode;
}) {
	const showDescription =
		description && description.trim().toLowerCase() !== summary.trim().toLowerCase();

	return (
		<div className="space-y-5 p-5" data-testid="workflow-inspect-panel">
			<div className="space-y-1.5">
				<p className="text-foreground text-sm font-medium">{summary}</p>
				{showDescription && (
					<p className="text-muted-foreground text-sm leading-relaxed">{description}</p>
				)}
			</div>

			<section>
				<SectionTitle>Steps</SectionTitle>
				<p className="text-muted-foreground text-sm">
					{stepsCount} step{stepsCount === 1 ? '' : 's'} in this workflow
				</p>
			</section>

			{footer}
		</div>
	);
}
