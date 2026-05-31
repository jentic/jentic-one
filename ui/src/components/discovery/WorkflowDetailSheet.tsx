/**
 * WorkflowDetailSheet
 *
 * Right-side slide-out panel for workspace workflow cards on the
 * Discover surface. Mirrors {@link ApiDetailSheet} so the two entity
 * types have parallel sheet UX.
 *
 * **Workspace-only.** Directory workflows have no per-workflow detail
 * to render — the catalog manifest is keyed by vendor folder, not by
 * individual workflow, and the actual Arazzo documents are only
 * fetched at import time (see `lazy_import_catalog_workflows`). Rather
 * than show a sheet that's effectively a duplicate of the API sheet
 * (same `api_id`, same `+ workflows` chip, same "add credential" CTA),
 * `DiscoveryView.handleCardClick` redirects directory workflow rows
 * straight to the API sheet for the underlying `api_id`. Deep links
 * to `?inspect_wf=catalog:workflows:<id>` are likewise rewritten by
 * the URL-fixup effect in `DiscoveryView`.
 *
 * Historical note: an earlier version of this sheet did render a
 * directory branch with a synthesised "Add credential to import"
 * card. After the May 2026 search-redundancy review (catalog workflow
 * rows are 1:1 with catalog API rows for the same `api_id` and carry
 * no per-workflow detail), the directory branch was collapsed away.
 *
 * One URL param drives it (owned by `DiscoveryView`):
 *   ?inspect_wf=<workspace_workflow_id>  → sheet open on this workflow
 *
 * Kept orthogonal to `?inspect=` (the API sheet param) so users can
 * have an API sheet and a workflow sheet driven by independent state
 * machines without ambiguity in the URL contract.
 *
 * Stability during close animation: the parent should keep `workflowId`
 * non-null until `onAfterClose` fires so content doesn't unmount
 * mid-animation. Same pattern as the API sheet's `stickyInspect`.
 */

import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Hash, Loader2, Workflow, X, Zap } from 'lucide-react';
import { VendorIcon } from './VendorIcon';
import { SectionTitle } from './SectionTitle';
import type { DiscoveryEntity } from './DiscoveryCard';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';
import { Button } from '@/components/ui/Button';
import { AppLink } from '@/components/ui/AppLink';
import { CopyButton } from '@/components/ui/CopyButton';
import { Markdown } from '@/components/ui/Markdown';
import { api } from '@/api/client';

// Number of steps shown inline before the "View full workflow" CTA. The
// sheet is meant to be a quick peek — long workflows shouldn't grow the
// sheet body to the point users have to scroll inside a side panel. The
// dedicated page is the right home for the complete step list, the
// Arazzo diagram, and the docs view.
const STEPS_PREVIEW_LIMIT = 5;

/**
 * True when the id is a directory (catalog) workflow id rather than a
 * workspace one. Workspace ids never start with `catalog:` so the
 * discriminator is total and pure. Exported so `DiscoveryView` can
 * rewrite stale deep links without round-tripping through the sheet.
 */
export function isDirectoryWorkflowId(id: string): boolean {
	return id.startsWith('catalog:');
}

export interface WorkflowDetailSheetProps {
	/**
	 * Workspace workflow id. Directory ids are rerouted upstream
	 * (`DiscoveryView`) to the API sheet, so this sheet only ever
	 * receives workspace ids in steady state. Null = sheet has no
	 * content.
	 */
	workflowId: string | null;
	/** Drives the SheetPrimitive open/close animation. */
	open: boolean;
	/**
	 * Optional cached entity from the discover list — lets the header
	 * render instantly with name/source instead of waiting for the
	 * workflow query to come back.
	 */
	initialEntity?: DiscoveryEntity;
	onClose: () => void;
	/** After the closing animation completes — parent uses this to drop
	 *  the sticky workflow id so the next open isn't stuck rendering
	 *  stale data. */
	onAfterClose?: () => void;
}

export function WorkflowDetailSheet({
	workflowId,
	open,
	initialEntity,
	onClose,
	onAfterClose,
}: WorkflowDetailSheetProps) {
	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy="workflow-detail-title"
		>
			{workflowId && !isDirectoryWorkflowId(workflowId) && (
				<WorkflowDetailSheetContent
					workflowId={workflowId}
					initialEntity={initialEntity}
					onClose={onClose}
				/>
			)}
		</SheetPrimitive>
	);
}

// ── Content ───────────────────────────────────────────────────────────────────

interface ContentProps {
	workflowId: string;
	initialEntity?: DiscoveryEntity;
	onClose: () => void;
}

function WorkflowDetailSheetContent({ workflowId, initialEntity, onClose }: ContentProps) {
	return (
		<div className="flex h-full flex-col">
			<SheetHeader
				title={initialEntity?.summary ?? workflowId}
				workflowId={workflowId}
				onClose={onClose}
			/>
			<div className="flex-1 overflow-y-auto">
				<WorkspaceBody workflowId={workflowId} initialEntity={initialEntity} />
			</div>
		</div>
	);
}

// ── Header ────────────────────────────────────────────────────────────────────

function SheetHeader({
	title,
	workflowId,
	onClose,
}: {
	title: string;
	workflowId: string;
	onClose: () => void;
}) {
	// Show a stable per-workflow identifier under the title so the user
	// knows which workflow they're looking at when names collide (rare,
	// but happens when the same Arazzo doc is registered twice with
	// different slugs). Workspace workflows surface their slug.
	const showId = workflowId !== title;

	return (
		<div className="border-border/60 bg-card sticky top-0 z-10 border-b">
			<div className="flex items-start gap-3 p-5">
				<div className="bg-muted text-foreground/80 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
					<Workflow className="h-5 w-5" aria-hidden="true" />
				</div>
				<div className="min-w-0 flex-1">
					<h2
						id="workflow-detail-title"
						className="text-foreground truncate text-base leading-tight font-semibold"
					>
						{title}
					</h2>
					{showId && (
						<div className="mt-0.5 flex items-center gap-1.5">
							<Hash size={11} className="text-muted-foreground/70 shrink-0" />
							<code className="text-muted-foreground truncate font-mono text-xs">
								{workflowId}
							</code>
							<CopyButton value={workflowId} ariaLabel="Copy workflow id" />
						</div>
					)}
					<div className="mt-2 flex flex-wrap items-center gap-1.5">
						<span
							className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-400 ring-1 ring-emerald-500/20"
							aria-label="Workspace workflow"
						>
							<Zap size={11} />
							Workspace
						</span>
					</div>
				</div>
				<Button
					variant="ghost"
					size="icon"
					onClick={onClose}
					className="shrink-0"
					aria-label="Close detail panel"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>
		</div>
	);
}

// ── Workspace body ────────────────────────────────────────────────────────────

/**
 * Decode a workspace workflow id into the slug used by the workflow
 * detail page route and `/workflows/{slug}` API. Workspace ids look
 * like `POST/{host}/workflows/{slug}` (see `workflow_capability_id`
 * server-side). Falls back to the input when the shape doesn't match
 * so deep links via raw slug still resolve.
 */
function workflowIdToSlug(workflowId: string): string {
	const m = workflowId.match(/\/workflows\/(.+)$/);
	return m ? m[1] : workflowId;
}

function WorkspaceBody({
	workflowId,
	initialEntity,
}: {
	workflowId: string;
	initialEntity?: DiscoveryEntity;
}) {
	const slug = workflowIdToSlug(workflowId);

	const { data, isLoading, error } = useQuery({
		queryKey: ['sheet-workflow', slug],
		queryFn: () => api.getWorkflow(slug),
		staleTime: 60_000,
		retry: 1,
	});

	if (isLoading) {
		return (
			<div className="flex h-40 items-center justify-center">
				<Loader2 className="text-muted-foreground h-5 w-5 animate-spin" />
			</div>
		);
	}

	if (error || !data) {
		return (
			<div className="space-y-3 p-5">
				<p className="text-danger text-sm">
					Failed to load workflow.{' '}
					{error instanceof Error && error.message ? `(${error.message})` : null}
				</p>
				<AppLink
					href={`/workspace/workflows/${encodeURIComponent(slug)}`}
					className="text-primary hover:text-primary/80 inline-flex items-center gap-1 text-sm font-medium"
				>
					Open the full page <ChevronRight size={14} />
				</AppLink>
			</div>
		);
	}

	// `getWorkflow` returns the digested workspace shape used by
	// `WorkflowDetailPage` — `{ name, description, steps[], involved_apis[] }`.
	const wf = data as {
		name?: string;
		description?: string;
		steps?: Array<{
			id?: string;
			step_id?: string;
			stepId?: string;
			operation?: string;
			operationPath?: string;
			operation_path?: string;
			operationId?: string;
			operation_id?: string;
			description?: string;
		}>;
		involved_apis?: string[];
	};
	const steps = Array.isArray(wf.steps) ? wf.steps : [];
	const involvedApis = Array.isArray(wf.involved_apis) ? wf.involved_apis : [];
	const description = (wf.description ?? initialEntity?.description ?? '').trim();

	const previewSteps = steps.slice(0, STEPS_PREVIEW_LIMIT);
	const hasMoreSteps = steps.length > previewSteps.length;

	return (
		<div className="space-y-5 p-5">
			{description ? (
				<Markdown
					source={description}
					className="text-muted-foreground text-sm leading-relaxed"
				/>
			) : null}

			<div className="flex flex-wrap items-center gap-2">
				<AppLink
					href={`/workspace/workflows/${encodeURIComponent(slug)}`}
					className="bg-primary text-primary-foreground hover:bg-primary/90 inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
					data-testid="wf-sheet-open-full"
				>
					Open full workflow <ChevronRight size={12} />
				</AppLink>
			</div>

			{involvedApis.length > 0 && (
				<section data-testid="wf-sheet-apis-section">
					<SectionTitle count={involvedApis.length}>APIs involved</SectionTitle>
					<ul className="-mx-1 flex flex-wrap gap-1.5">
						{involvedApis.map((apiId) => (
							<li key={apiId}>
								<AppLink
									href={`/discover?inspect=${encodeURIComponent(apiId)}`}
									className="border-border/40 hover:border-primary/40 hover:bg-muted/50 group inline-flex items-center gap-2 rounded-lg border px-2 py-1 transition-colors"
								>
									<VendorIcon name={apiId} vendor={apiId} size="sm" />
									<span className="text-foreground group-hover:text-primary text-xs font-medium">
										{apiId}
									</span>
								</AppLink>
							</li>
						))}
					</ul>
				</section>
			)}

			<section data-testid="wf-sheet-steps-section">
				<SectionTitle count={steps.length}>Steps</SectionTitle>
				{steps.length === 0 ? (
					<p className="text-muted-foreground text-sm italic">
						This workflow has no steps yet.
					</p>
				) : (
					<>
						<ol className="space-y-2" data-testid="wf-sheet-steps">
							{previewSteps.map((step, i) => (
								<StepPreviewRow
									key={step.id ?? step.stepId ?? i}
									index={i}
									step={step}
								/>
							))}
						</ol>
						{hasMoreSteps && (
							<AppLink
								href={`/workspace/workflows/${encodeURIComponent(slug)}`}
								className="text-primary hover:text-primary/80 mt-3 inline-flex items-center gap-1 text-xs font-medium"
								data-testid="wf-sheet-more-steps"
							>
								See {steps.length - previewSteps.length} more step
								{steps.length - previewSteps.length === 1 ? '' : 's'}{' '}
								<ChevronRight size={12} />
							</AppLink>
						)}
					</>
				)}
			</section>
		</div>
	);
}

/**
 * Compact one-line step preview. Intentionally lighter than the full
 * `StepRow` on `WorkflowDetailPage` — the sheet is for skim, not for
 * deep operation drill-down. The full page handles params / outputs /
 * success criteria; here we just show "step N · stepId · description".
 */
function StepPreviewRow({
	index,
	step,
}: {
	index: number;
	step: {
		id?: string;
		step_id?: string;
		stepId?: string;
		operationId?: string;
		operation_id?: string;
		description?: string;
	};
}) {
	const stepId = step.stepId ?? step.step_id ?? step.id;
	const description = (step.description ?? '').trim();
	return (
		<li
			className="border-border/50 bg-muted/30 flex items-start gap-3 rounded-lg border p-3"
			data-testid="wf-sheet-step-row"
		>
			<span
				className="bg-card text-muted-foreground border-border/60 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[10px] tabular-nums"
				aria-hidden="true"
			>
				{index + 1}
			</span>
			<div className="min-w-0 flex-1">
				{stepId ? (
					<p className="text-foreground truncate text-sm font-medium">{stepId}</p>
				) : null}
				{description ? (
					<p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-relaxed">
						{description}
					</p>
				) : null}
			</div>
		</li>
	);
}
