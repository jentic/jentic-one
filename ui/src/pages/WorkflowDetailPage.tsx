import { Component, lazy, Suspense, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import {
	AlertTriangle,
	ChevronRight,
	ExternalLink,
	Hash,
	Trash2,
	Workflow,
	Zap,
} from 'lucide-react';
import { api, apiUrl } from '@/api/client';
import { Badge, MethodBadge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { BackButton } from '@/components/ui/BackButton';
import { ConfirmDeleteDialog } from '@/components/ui/ConfirmDeleteDialog';
import { CopyButton } from '@/components/ui/CopyButton';
import { AppLink } from '@/components/ui/AppLink';
import { KeyboardShortcutsBar, MOD_KEY } from '@/components/ui/KeyboardShortcutsBar';
import { LoadingState } from '@/components/ui/LoadingState';
import { PageHeader } from '@/components/ui/PageHeader';
import { PageHelp } from '@/components/ui/PageHelp';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { PageShell } from '@/components/layout/PageShell';
import { VendorIcon } from '@/components/discovery/VendorIcon';
import { WorkflowMetaStrip } from '@/components/workflows/WorkflowMetaStrip';
import { useScrollRestore } from '@/hooks/useScrollRestore';
import { isTypingTarget } from '@/lib/keyboard';
import '@jentic/arazzo-ui/styles.css';

// Arazzo embeds its own runtime. Code-split it so the page shell paints
// quickly and we only pay for the viewer when a Diagram / Docs / Split
// tab actually mounts. Stylesheet stays eager (above) — it's tiny and
// referenced by a couple of other surfaces.
const ArazzoUI = lazy(async () => {
	const mod = await import('@jentic/arazzo-ui');
	return { default: mod.ArazzoUI };
});

type ArazzoView = 'diagram' | 'docs' | 'split';
type DetailTab = 'overview' | ArazzoView;

const TAB_OPTIONS: { value: DetailTab; label: string }[] = [
	{ value: 'overview', label: 'Overview' },
	{ value: 'diagram', label: 'Diagram' },
	{ value: 'docs', label: 'Docs' },
	{ value: 'split', label: 'Split' },
];

function isDetailTab(value: string | null): value is DetailTab {
	return value === 'overview' || value === 'diagram' || value === 'docs' || value === 'split';
}

class ArazzoErrorBoundary extends Component<
	{ slug?: string; children: ReactNode },
	{ error: Error | null }
> {
	state = { error: null as Error | null };
	static getDerivedStateFromError(error: Error) {
		return { error };
	}
	componentDidUpdate(prevProps: { slug?: string }) {
		if (prevProps.slug !== this.props.slug && this.state.error) {
			this.setState({ error: null });
		}
	}
	render() {
		if (this.state.error) {
			return (
				<div className="border-border bg-muted rounded-xl border p-8 text-center">
					<AlertTriangle className="text-warning mx-auto mb-3 h-8 w-8" />
					<p className="text-foreground mb-1 text-sm font-medium">
						Workflow visualization failed to render
					</p>
					<p className="text-muted-foreground text-xs">{this.state.error.message}</p>
				</div>
			);
		}
		return this.props.children;
	}
}

const HELP = (
	<PageHelp
		title="About this workflow"
		intro={
			<p>
				An Arazzo workflow is a sequence of API calls choreographed across one or more APIs
				— think "register a customer, charge their card, email a receipt" as a single
				declarative document. Each step calls a real operation on a real API, so the
				workflow is fully executable from this page.
			</p>
		}
		sections={[
			{
				heading: 'Overview',
				body: (
					<p>
						Skim the workflow at a glance: description, the APIs it touches, and an
						ordered list of its steps. Click an API chip to inspect that API directly in
						Discover.
					</p>
				),
			},
			{
				heading: 'Diagram / Docs / Split',
				body: (
					<p>
						The same Arazzo document, three ways. <strong>Diagram</strong> is the visual
						flow, <strong>Docs</strong> is the human-readable spec, and{' '}
						<strong>Split</strong> shows both side-by-side for deep-dive reviews. Your
						choice is preserved in the URL via{' '}
						<code className="text-foreground">?view=&hellip;</code>.
					</p>
				),
			},
			{
				heading: 'Catalog vs Local',
				body: (
					<p>
						Workflows from the Jentic public catalog can be browsed without importing.
						Click <strong>Import</strong> on a catalog workflow to bring it into your
						workspace — the steps then become executable using your credentials.
					</p>
				),
			},
		]}
		links={[
			{ href: 'https://www.openapis.org/arazzo-specification', label: 'What is Arazzo?' },
			{
				href: 'https://github.com/jentic/jentic-public-apis/tree/main/workflows',
				label: 'Browse the catalog on GitHub',
			},
		]}
		shortcuts={[
			{ keys: ['Esc'], label: 'Go back to workspace' },
			{ keys: [MOD_KEY, '/'], chord: true, label: 'Show this help' },
		]}
	/>
);

export default function WorkflowDetailPage() {
	const { slug } = useParams<{ slug: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [searchParams, setSearchParams] = useSearchParams();
	useScrollRestore();

	const [deleteOpen, setDeleteOpen] = useState(false);

	useEffect(() => {
		function onKeyDown(e: KeyboardEvent) {
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			if (isTypingTarget(e.target)) return;
			if (e.key === 'Escape' && !deleteOpen && !document.querySelector('dialog[open]')) {
				e.preventDefault();
				navigate('/workspace');
			}
		}
		document.addEventListener('keydown', onKeyDown);
		return () => document.removeEventListener('keydown', onKeyDown);
	}, [navigate, deleteOpen]);

	const deleteMutation = useMutation({
		mutationFn: () => api.deleteWorkflow(slug!),
		onSuccess: async () => {
			await Promise.all([
				queryClient.invalidateQueries({ queryKey: ['workflows'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace'] }),
				queryClient.invalidateQueries({ queryKey: ['workspace-stats'] }),
				queryClient.invalidateQueries({ queryKey: ['apis'] }),
			]);
			navigate('/workspace');
		},
		onError: () => {
			// Keep dialog open — the loading state resets automatically
		},
	});

	const tab = ((): DetailTab => {
		const v = searchParams.get('view');
		return isDetailTab(v) ? v : 'overview';
	})();
	const setTab = (next: DetailTab) => {
		setSearchParams(
			(prev) => {
				const p = new URLSearchParams(prev);
				if (next === 'overview') {
					p.delete('view');
				} else {
					p.set('view', next);
				}
				return p;
			},
			{ replace: true },
		);
	};

	const {
		data: workflow,
		isLoading,
		error,
	} = useQuery({
		queryKey: ['workflow', slug],
		queryFn: () => api.getWorkflow(slug!),
		enabled: !!slug,
		retry: (failureCount, err: any) => err?.status !== 404 && failureCount < 2,
	});

	const showArazzo = tab === 'diagram' || tab === 'docs' || tab === 'split';

	const { data: arazzoDoc, isLoading: isLoadingArazzo } = useQuery({
		queryKey: ['workflow-arazzo', slug],
		queryFn: async () => {
			const res = await fetch(apiUrl(`/workflows/${slug}`), {
				headers: { Accept: 'application/vnd.oai.workflows+json' },
				credentials: 'include',
			});
			if (!res.ok) throw new Error('Failed to fetch Arazzo document');
			return res.json();
		},
		// Lazy-fetch: only paid for once the user opens an Arazzo tab.
		enabled: !!slug && !!workflow && showArazzo,
	});

	if (isLoading)
		return (
			<PageShell>
				<LoadingState message="Loading workflow..." />
			</PageShell>
		);

	const is404 = (error as any)?.status === 404;
	if (error && !is404) {
		return (
			<PageShell>
				<PageHeader title="Workflow" />
				<BackButton to="/workspace" label="Back" />
				<div className="py-16 text-center">
					<AlertTriangle className="text-danger mx-auto mb-3 h-8 w-8" />
					<p className="text-foreground text-sm font-medium">Failed to load workflow</p>
					<p className="text-muted-foreground mt-1 text-xs">
						{(error as any)?.message || 'Unknown error'}
					</p>
				</div>
			</PageShell>
		);
	}

	if (!workflow) return <CatalogWorkflowFallback slug={slug!} navigate={navigate} />;

	const steps: any[] = workflow.steps ?? [];
	const involvedApis: string[] = workflow.involved_apis ?? [];
	const resolvedTitle = workflow.name ?? workflow.slug;
	const showDescription =
		workflow.description &&
		workflow.description !== workflow.name &&
		workflow.description !== workflow.slug;

	return (
		<>
			<PageShell width="wide">
				<PageHeader
					title={resolvedTitle}
					subtitle={showDescription ? workflow.description : undefined}
					icon={
						<div className="bg-accent-teal/10 flex h-10 w-10 items-center justify-center rounded-lg">
							<Workflow className="text-accent-teal h-5 w-5" />
						</div>
					}
					actions={
						<div className="flex items-center gap-2">
							<Button variant="danger" size="sm" onClick={() => setDeleteOpen(true)}>
								<Trash2 className="h-3.5 w-3.5" />
							</Button>
							{HELP}
						</div>
					}
				/>

				<BackButton to="/workspace" label="Back" />

				<WorkflowMetaStrip
					slug={slug!}
					stepsCount={steps.length}
					involvedApis={involvedApis}
				/>

				<div className="flex flex-wrap items-center justify-between gap-3">
					<SlugChip slug={workflow.slug} />
					<SegmentedToggle
						layoutId="workflow-detail-view"
						value={tab}
						onChange={setTab}
						options={TAB_OPTIONS}
					/>
				</div>

				{tab === 'overview' ? (
					<OverviewBody steps={steps} involvedApis={involvedApis} />
				) : isLoadingArazzo ? (
					<LoadingState message="Loading workflow visualization..." />
				) : arazzoDoc ? (
					<ArazzoErrorBoundary slug={slug}>
						<div
							className="border-border bg-muted overflow-hidden rounded-xl border"
							style={{ height: 'min(75vh, 800px)' }}
							data-testid="workflow-arazzo-frame"
						>
							<Suspense
								fallback={
									<LoadingState message="Loading workflow visualization..." />
								}
							>
								<ArazzoUI
									document={arazzoDoc}
									view={tab as ArazzoView}
									onViewChange={(v: ArazzoView) => setTab(v)}
								/>
							</Suspense>
						</div>
					</ArazzoErrorBoundary>
				) : (
					<div className="text-muted-foreground py-16 text-center">
						Failed to load workflow visualization.
					</div>
				)}

				<ConfirmDeleteDialog
					target={slug ? { kind: 'workflow', slug, name: workflow.name ?? slug } : null}
					open={deleteOpen}
					onClose={() => setDeleteOpen(false)}
					onConfirm={() => deleteMutation.mutate()}
					loading={deleteMutation.isPending}
				/>
			</PageShell>

			<KeyboardShortcutsBar
				shortcuts={[
					{ keys: ['Esc'], label: 'back' },
					{ keys: [MOD_KEY, '/'], chord: true, label: 'help' },
				]}
			/>
		</>
	);
}

function SlugChip({ slug }: { slug: string }) {
	return (
		<span
			className="border-border/50 bg-muted/40 hover:border-border hover:bg-muted/60 inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md border py-0.5 pr-0.5 pl-2 font-mono text-[11px] transition-colors"
			data-testid="workflow-slug"
			title={slug}
		>
			<Hash size={11} aria-hidden="true" className="text-muted-foreground/70 shrink-0" />
			<span className="text-foreground/90 min-w-0 truncate">{slug}</span>
			<CopyButton
				value={slug}
				size="icon"
				variant="ghost"
				toastMessage="Slug copied"
				ariaLabel="Copy slug"
				className="text-muted-foreground/70 hover:text-foreground h-6 w-6 p-0 [&_svg]:h-3 [&_svg]:w-3"
			/>
		</span>
	);
}

function OverviewBody({ steps, involvedApis }: { steps: any[]; involvedApis: string[] }) {
	return (
		<div className="grid gap-4 md:grid-cols-3" data-testid="workflow-overview">
			<section
				className="border-border/60 bg-card rounded-xl border p-5 md:col-span-2"
				data-testid="workflow-overview-steps-card"
			>
				<div className="flex items-baseline justify-between">
					<h2 className="text-foreground text-sm font-semibold">Operations</h2>
					<span className="text-muted-foreground text-xs">
						{steps.length} step{steps.length === 1 ? '' : 's'}
					</span>
				</div>

				{steps.length === 0 ? (
					<p className="text-muted-foreground mt-4 text-xs italic">
						This workflow has no steps yet.
					</p>
				) : (
					<ol className="mt-4 space-y-2" data-testid="workflow-overview-steps">
						{steps.map((step, i) => (
							<StepRow
								key={step.stepId ?? step.step_id ?? step.id ?? step.name ?? i}
								index={i}
								isLast={i === steps.length - 1}
								step={step}
								involvedApis={involvedApis}
							/>
						))}
					</ol>
				)}
			</section>

			<aside className="space-y-4">
				<section
					className="border-border/60 bg-card rounded-xl border p-5"
					data-testid="workflow-overview-apis"
				>
					<div className="flex items-baseline justify-between">
						<h2 className="text-foreground text-sm font-semibold">APIs involved</h2>
						<span className="text-muted-foreground text-xs">{involvedApis.length}</span>
					</div>
					{involvedApis.length === 0 ? (
						<p className="text-muted-foreground mt-3 text-xs italic">
							No APIs declared.
						</p>
					) : (
						<ul className="mt-3 space-y-1.5">
							{involvedApis.map((apiId) => (
								<li key={apiId}>
									<AppLink
										href={`/discover?inspect=${encodeURIComponent(apiId)}`}
										className="border-border/40 hover:border-primary/40 hover:bg-muted/50 group flex items-center gap-2.5 rounded-lg border p-2 transition-colors"
										data-testid="workflow-overview-api-chip"
										data-api-id={apiId}
									>
										<VendorIcon name={apiId} vendor={apiId} size="sm" />
										<span className="text-foreground group-hover:text-primary truncate text-xs font-medium">
											{apiId}
										</span>
									</AppLink>
								</li>
							))}
						</ul>
					)}
				</section>
			</aside>
		</div>
	);
}

/**
 * One row in the Overview Steps list. Each step is an Arazzo step
 * (`stepId` + an underlying OpenAPI operation), so the row's title is
 * the `stepId` itself — never "Step N", which loses information. Below
 * the title we surface the operationId, the API it calls into, and
 * quiet counters for parameters / outputs / successCriteria so a long
 * workflow scans like a checklist.
 *
 * When we can resolve which API the step targets, the whole row is a
 * link into Discover's API detail sheet (`/discover?inspect=<api>&op=
 * <operationId>`), letting the user drill from "what does this
 * workflow do" to "what does this individual operation look like".
 */
function StepRow({
	index,
	isLast,
	step,
	involvedApis,
}: {
	index: number;
	isLast: boolean;
	step: any;
	involvedApis: string[];
}) {
	// Workflow steps come back from the API in the shape
	// `{ id, operation, description }` (see WorkflowStep in types.ts),
	// but some Arazzo flavours use camelCase (`stepId`, `operationId`,
	// `operationPath`). We accept both so this row works against either
	// the digested workflow payload and the raw Arazzo document.
	const stepId: string | undefined =
		step.stepId ?? step.step_id ?? step.id ?? step.name ?? undefined;
	const operationId: string | undefined = step.operationId ?? step.operation_id ?? undefined;
	const operationPath: string | undefined =
		step.operationPath ?? step.operation_path ?? step.operation ?? undefined;
	const description: string | undefined =
		typeof step.description === 'string' ? step.description : undefined;
	const summary: string | undefined =
		typeof step.summary === 'string' && step.summary !== stepId ? step.summary : undefined;

	// Headline = stepId (the actual identifier the workflow author chose),
	// falling back to summary or "Step N" only when the step doesn't carry
	// a stepId (rare; happens for malformed Arazzo).
	const title = stepId ?? summary ?? `Step ${index + 1}`;
	const showSummaryAsCaption = summary && summary !== title;

	// Try to resolve the API host the step calls into. Two shapes we
	// see in real Arazzo: dotted `vendor.operationId` or a JSON Pointer
	// in `operationPath` of the form `$sourceDescriptions.<vendor>.url#…`.
	// The raw key (`zendesk_api`, `slack`) isn't useful for VendorIcon
	// lookup though — we need the involved-APIs domain (`zendesk.com`).
	const rawApiHint: string | undefined = (() => {
		if (operationId && operationId.includes('.')) return operationId.split('.')[0];
		if (operationPath) {
			const m = operationPath.match(/\$sourceDescriptions\.([^.]+)\.url/);
			if (m) return m[1];
		}
		return undefined;
	})();

	// Match the raw hint to one of the workflow's declared `involved_apis`
	// so we can render the VendorIcon and proper domain label. Uses the
	// SLD as the join key (zendesk_api → zendesk.com via "zendesk").
	const apiId: string | undefined = (() => {
		if (!rawApiHint) return undefined;
		const needle = rawApiHint.toLowerCase().replace(/[_-]?api$/i, '');
		const exact = involvedApis.find((a) => a.toLowerCase() === rawApiHint.toLowerCase());
		if (exact) return exact;
		const byPrefix = involvedApis.find((a) => a.toLowerCase().startsWith(needle));
		return byPrefix ?? rawApiHint;
	})();

	// When a step references a raw OpenAPI operation by path/method
	// (e.g. `…#/paths/~1api~1v2~1tickets~1{ticket_id}/get`) we don't
	// have a real operationId to display, but we *can* surface a
	// `MethodBadge + path` token so the row reads like a Discover row.
	const operationCall: { method: string; path: string } | null = (() => {
		if (!operationPath) return null;
		const m = operationPath.match(/#\/paths\/([^/]+)\/([a-z]+)$/i);
		if (!m) return null;
		const path = decodeURIComponent(m[1].replace(/~1/g, '/').replace(/~0/g, '~'));
		return { method: m[2].toUpperCase(), path };
	})();

	// `parameters` is an array of {name, in, value}; `successCriteria` is
	// an array of {condition}; `outputs` is a map name→JSONPath. Any of
	// them may be missing on minimally-specified steps.
	const paramsCount = Array.isArray(step.parameters) ? step.parameters.length : 0;
	const criteriaCount = Array.isArray(step.successCriteria) ? step.successCriteria.length : 0;
	const outputsCount =
		step.outputs && typeof step.outputs === 'object' ? Object.keys(step.outputs).length : 0;

	const canNavigate = Boolean(apiId && operationId);
	const href = canNavigate
		? `/discover?inspect=${encodeURIComponent(apiId!)}&op=${encodeURIComponent(operationId!)}`
		: undefined;

	const rowBodyClass = canNavigate
		? 'group border-border/50 bg-background/40 hover:border-primary/40 hover:bg-muted/40 focus-visible:border-primary/40 focus-visible:bg-muted/40 block min-w-0 rounded-lg border p-3 text-left no-underline transition-colors focus-visible:outline-none'
		: 'border-border/50 bg-background/40 block min-w-0 rounded-lg border p-3 text-left';

	const body = (
		<StepRowBody
			title={title}
			summary={showSummaryAsCaption ? summary : undefined}
			operationId={operationId}
			operationCall={operationCall}
			apiId={apiId}
			description={description}
			paramsCount={paramsCount}
			outputsCount={outputsCount}
			criteriaCount={criteriaCount}
			canNavigate={canNavigate}
		/>
	);

	return (
		<li
			className="relative grid grid-cols-[24px_1fr] gap-3"
			data-testid="workflow-step"
			data-step-id={stepId}
		>
			<div className="relative flex justify-center pt-3">
				<span className="bg-muted text-muted-foreground border-border/60 relative z-10 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-semibold">
					{index + 1}
				</span>
				{!isLast ? (
					<span
						aria-hidden="true"
						className="bg-border/60 absolute top-10 bottom-[-8px] left-1/2 w-px -translate-x-1/2"
					/>
				) : null}
			</div>

			{canNavigate ? (
				<AppLink
					href={href!}
					className={rowBodyClass}
					aria-label={`Open ${title} in Discover`}
					data-testid="workflow-step-body"
				>
					{body}
				</AppLink>
			) : (
				<div className={rowBodyClass} data-testid="workflow-step-body">
					{body}
				</div>
			)}
		</li>
	);
}

function StepRowBody({
	title,
	summary,
	operationId,
	operationCall,
	apiId,
	description,
	paramsCount,
	outputsCount,
	criteriaCount,
	canNavigate,
}: {
	title: string;
	summary?: string;
	operationId?: string;
	operationCall: { method: string; path: string } | null;
	apiId?: string;
	description?: string;
	paramsCount: number;
	outputsCount: number;
	criteriaCount: number;
	canNavigate: boolean;
}) {
	return (
		<>
			<div className="flex min-w-0 items-center gap-2">
				{apiId ? (
					<VendorIcon name={apiId} vendor={apiId} size="sm" className="shrink-0" />
				) : null}
				<div className="min-w-0 flex-1">
					<p className="text-foreground truncate font-mono text-sm font-semibold">
						{title}
					</p>
					{apiId ? (
						<p className="text-muted-foreground mt-0.5 truncate text-[11px]">{apiId}</p>
					) : null}
				</div>
				{canNavigate ? (
					<ChevronRight
						size={14}
						aria-hidden="true"
						className="text-muted-foreground/30 group-hover:text-muted-foreground/80 mt-1 shrink-0 transition-colors"
					/>
				) : null}
			</div>

			{summary ? <p className="text-foreground/80 mt-2 text-sm">{summary}</p> : null}

			{operationCall || operationId ? (
				<div className="mt-2 flex min-w-0 items-center gap-2">
					{operationCall ? (
						<>
							<MethodBadge method={operationCall.method} />
							<code className="text-muted-foreground/90 min-w-0 truncate font-mono text-[11px]">
								{operationCall.path}
							</code>
						</>
					) : operationId ? (
						<code className="bg-muted text-muted-foreground/90 border-border/40 min-w-0 truncate rounded border px-1.5 py-0.5 font-mono text-[11px]">
							{operationId}
						</code>
					) : null}
				</div>
			) : null}

			{description ? (
				<p className="text-muted-foreground mt-2 text-xs leading-relaxed">{description}</p>
			) : null}

			{paramsCount + outputsCount + criteriaCount > 0 ? (
				<div
					className="text-muted-foreground/80 border-border/40 mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-2 text-[10px]"
					data-testid="workflow-step-meta"
				>
					{paramsCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{paramsCount}</span>{' '}
							param{paramsCount === 1 ? '' : 's'}
						</span>
					) : null}
					{outputsCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{outputsCount}</span>{' '}
							output{outputsCount === 1 ? '' : 's'}
						</span>
					) : null}
					{criteriaCount > 0 ? (
						<span>
							<span className="text-foreground/80 font-medium">{criteriaCount}</span>{' '}
							check{criteriaCount === 1 ? '' : 's'}
						</span>
					) : null}
				</div>
			) : null}
		</>
	);
}

function CatalogWorkflowFallback({
	slug,
	navigate,
}: {
	slug: string;
	navigate: (path: string) => void;
}) {
	const queryClient = useQueryClient();
	const [importing, setImporting] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const apiId = slug.replace('~', '/');
	const githubUrl = `https://github.com/jentic/jentic-public-apis/tree/main/workflows/${slug}`;
	const encodedSlug = encodeURIComponent(slug);
	const rawArazzoUrl = `https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main/workflows/${encodedSlug}/workflows.arazzo.json`;
	const arazzoUIUrl = `https://arazzo-ui.jentic.com?document=${encodeURIComponent(rawArazzoUrl)}`;

	const handleImport = async () => {
		setImporting(true);
		setError(null);
		try {
			const catalogRes = await fetch(apiUrl(`/catalog/${apiId}`), { credentials: 'include' });
			if (!catalogRes.ok) {
				const body = await catalogRes.json().catch(() => ({}));
				throw new Error(body.detail || `Catalog lookup failed (${catalogRes.status})`);
			}
			const catalogEntry = await catalogRes.json();
			if (!catalogEntry.spec_url) {
				throw new Error('No spec URL found for this API in the catalog');
			}
			const importRes = await fetch(apiUrl('/import'), {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					sources: [{ type: 'url', url: catalogEntry.spec_url, force_api_id: apiId }],
				}),
			});
			if (!importRes.ok) {
				const body = await importRes.json().catch(() => ({}));
				throw new Error(body.detail || `Import failed (${importRes.status})`);
			}
			const importResult = await importRes.json();
			if (importResult.failed > 0) {
				const err = importResult.results?.[0]?.error || 'Unknown error';
				throw new Error(`Import failed: ${err}`);
			}
			queryClient.invalidateQueries({ queryKey: ['workflows'] });
			navigate('/workspace');
		} catch (e: any) {
			setError(e.message);
		} finally {
			setImporting(false);
		}
	};

	const headerActions = (
		<>
			<Button
				variant="primary"
				size="sm"
				onClick={handleImport}
				loading={importing}
				data-testid="workflow-catalog-import"
			>
				<Zap size={14} aria-hidden="true" />
				{importing ? 'Importing…' : 'Import this workflow'}
			</Button>
			<AppLink
				href={arazzoUIUrl}
				className="border-border text-muted-foreground hover:text-foreground hover:bg-muted/40 inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors"
			>
				<ExternalLink size={12} aria-hidden="true" /> Open in Arazzo UI
			</AppLink>
			{HELP}
		</>
	);

	return (
		<PageShell width="wide">
			<PageHeader
				title={apiId}
				subtitle="From the Jentic public catalog. Import to inspect and execute."
				actions={headerActions}
			/>

			<BackButton to="/workspace" label="Back" />

			<div className="flex items-center gap-3">
				<Badge
					variant="default"
					className="bg-accent-yellow/10 text-accent-yellow border-accent-yellow/20"
				>
					<Workflow size={11} aria-hidden="true" /> Catalog workflow
				</Badge>
				<p className="text-muted-foreground font-mono text-[11px]">{slug}</p>
			</div>

			{error ? (
				<div
					className="border-danger/30 bg-danger/10 text-danger rounded-lg border p-3 text-xs"
					data-testid="workflow-catalog-import-error"
				>
					{error}
				</div>
			) : null}

			<section className="border-border/60 bg-card grid gap-3 rounded-xl border p-5 text-sm">
				<p className="text-muted-foreground">
					This workflow lives in the Jentic public catalog. Import it to bring its steps
					into your workspace; once imported you'll see the diagram, docs, and run
					controls here.
				</p>
				<ul className="text-muted-foreground space-y-2 text-xs">
					<li>
						<AppLink
							href={githubUrl}
							className="text-primary hover:text-primary/80 inline-flex items-center gap-1.5"
						>
							<ExternalLink size={12} aria-hidden="true" /> View source on GitHub
						</AppLink>
					</li>
				</ul>
			</section>
		</PageShell>
	);
}
