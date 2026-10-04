/**
 * WorkspaceDockPanel — the "Your workspace" panel docked beside the Library
 * catalog (sticky on xl, like Monitor's Live activity panel). High-level and
 * human: each block answers one question, in the order an operator asks it.
 *
 *   1. Does anything need me?   — only non-zero attention items (collapsible,
 *                                 `NeedsAttention`), else "All good"
 *   2. What's in my workspace?  — the FULL list (newest first), narrowed by a
 *                                 text filter + Live / Draft / Update toggle
 *                                 (`?q=` / `?status=` on `/library`); the
 *                                 catalog's in-flight imports sit at its top as
 *                                 "Adding…" rows; each row links to the API's hub
 *   3. What just happened?      — API events off the shell's live stream
 *                                 (collapsed under the list)
 *
 * This panel IS the workspace view (`/library/workspace` redirects here).
 * The title and the footer stay put; only the body scrolls, so it fits the
 * viewport like the sticky dock it is. Calm by design: one tonal surface, no
 * borders or dividers, small caps section labels, quiet meta lines.
 *
 * Every figure comes from {@link useWorkspaceDigest} (real registry, credential
 * and usage reads) or the live event stream; a signal whose read hasn't
 * answered — or isn't readable for this user — is omitted, never zeroed.
 *
 * A "no credential" API name is a button, not a link: it opens the shared
 * Add credential flow in place, on that API's form (the host owns the flow —
 * `usePanelCredentialFlow`), and the panel confirms a usable credential with a
 * transient "Credential added" row at the top.
 *
 * Drag-to-add: the host's drag hook passes `drop` while a catalog row is being
 * dragged; the panel lights up (`data-drag`) and shows a drop slot at the top
 * of the list. `justAddedApiIds` flashes the rows that just landed.
 */
import { memo, useId, useMemo, useState, type Ref } from 'react';
import {
	ArrowUpCircle,
	Bot,
	CheckCircle2,
	ChevronDown,
	Filter,
	KeyRound,
	Plus,
	Upload,
	X,
	Zap,
} from 'lucide-react';
import {
	API_STATE_LABELS,
	AppLink,
	Button,
	ErrorAlert,
	MetaLine,
	SearchInput,
	SectionLabel,
	SegmentedToggle,
	Skeleton,
	StatusText,
	StreamEventRow,
	VendorIcon,
	apiServingState,
	type MetaLineItem,
} from '@/shared/ui';
import {
	callsInWeek,
	isCredentialMissing,
	useAgentFigures,
} from '@/shared/credentials/api/apiHealth';
import { useAgentStreamOptional, vendorIconPropsFor } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { newestFirst } from '@/shared/lib/newestFirst';
import type { WorkspaceDigest, WorkspaceDigestRow } from '@/modules/discover/api';
import { NeedsAttention } from '@/modules/discover/components/NeedsAttention';
import type { CredentialAddedNotice } from '@/modules/discover/components/usePanelCredentialFlow';
import {
	matchesStatus,
	matchesText,
	useWorkspaceListFilter,
	type WorkspaceStatusFilter,
} from '@/modules/discover/lib/workspaceListFilter';
import type { DragDropState } from '@/modules/discover/lib/useDragToAdd';

const RECENT_LIMIT = 5;

/** The usage figure's hover title (usage is grouped by vendor/name). */
const USAGE_TITLE = 'Calls in the last 7 days (all versions)';

/**
 * A list row's shape, shared by API rows and "Adding…" rows: 28px mark, name
 * + meta, a right column. Bleeds 8px into the panel padding so its hover
 * fill lines up with the text above it.
 */
const ROW_CLASS =
	'rounded-field -mx-2 grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-2.5 p-2';

function plural(n: number, noun: string): string {
	return `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`;
}

function ApiRow({
	row,
	agentCount,
	agentsAtLeast,
	showUsage,
	usageExhaustive,
	justAdded,
}: {
	row: WorkspaceDigestRow;
	/** Agents with access; null while loading or unknowable (then omitted). */
	agentCount: number | null;
	/** `agentCount` is a floor (more bound agents than one read page) — "N+". */
	agentsAtLeast: boolean;
	showUsage: boolean;
	usageExhaustive: boolean;
	/** Just landed in the workspace — a one-off green wash. */
	justAdded: boolean;
}) {
	const calls = callsInWeek(row.usage, usageExhaustive);
	const failed = row.usage?.failed ?? 0;
	// Only with every credential page loaded (`credentialCount` non-null) may
	// "No credential" be claimed.
	const credentialMissing = isCredentialMissing(row.needsAuth, row.credentialCount);
	const { serving, updateAvailable } = apiServingState(row);

	// state · 🤖 agents · ⚡ ops · 🔑 credentials (credentials last, so a missing
	// one leaves the rest aligned) · "Update available".
	const meta: MetaLineItem[] = [
		{ key: 'state', value: API_STATE_LABELS[serving], testId: `api-state-${serving}` },
	];
	if (agentCount != null) {
		const n = `${agentCount}${agentsAtLeast ? '+' : ''}`;
		const label = `${n} ${agentCount === 1 && !agentsAtLeast ? 'agent' : 'agents'} with access`;
		meta.push({
			key: 'agents',
			icon: <Bot />,
			value: n,
			label,
			title: label,
			testId: 'workspace-panel-api-agents',
		});
	}
	if (row.operationCount != null) {
		const label = plural(row.operationCount, 'operation');
		meta.push({
			key: 'ops',
			icon: <Zap />,
			value: row.operationCount.toLocaleString(),
			label,
			title: label,
			testId: 'workspace-panel-api-ops',
		});
	}
	if (credentialMissing) {
		meta.push({
			key: 'creds',
			icon: <KeyRound />,
			value: 'No credential',
			title: 'No credential — agents can’t call it',
			// Grey word, muted-ochre key: noted, not alarming (Needs attention
			// above already lists it as the thing to do).
			tone: 'text-foreground-sub',
			iconTone: 'text-caution',
			testId: 'workspace-panel-api-no-credential',
		});
	} else if (row.credentialCount != null && row.credentialCount > 0) {
		const label = plural(row.credentialCount, 'credential');
		meta.push({
			key: 'creds',
			icon: <KeyRound />,
			value: row.credentialCount.toLocaleString(),
			label,
			title: label,
			testId: 'workspace-panel-api-credentials',
		});
	}
	if (updateAvailable) {
		meta.push({
			key: 'update',
			icon: <ArrowUpCircle />,
			value: API_STATE_LABELS.update,
			tone: 'text-foreground-sub',
			iconTone: 'text-caution',
			testId: 'api-state-update',
		});
	}

	return (
		<li>
			<AppLink
				href={row.href}
				className={cn(
					ROW_CLASS,
					'hover:bg-surface-field transition-colors duration-[140ms]',
					justAdded && 'animate-flash-added',
				)}
				data-testid="workspace-panel-api"
				data-just-added={justAdded || undefined}
			>
				<VendorIcon
					{...vendorIconPropsFor({
						title: row.title,
						host: row.host,
						vendor: row.ref.vendor,
						iconUrl: row.iconUrl,
					})}
					size="sm"
				/>
				<div className="min-w-0">
					<p className="text-foreground-name truncate text-[13.5px] font-semibold">
						{row.title}
					</p>
					<MetaLine items={meta} className="text-foreground-faint mt-0.5 text-xs" />
				</div>
				{showUsage && calls != null ? (
					<span
						className="text-foreground-faint text-right font-mono text-[11px] leading-[1.35]"
						title={USAGE_TITLE}
					>
						<span className="block">{plural(calls, 'call')}</span>
						{failed > 0 && (
							<span
								className="text-danger block"
								data-testid="workspace-panel-api-failed"
							>
								{failed.toLocaleString()} failed
							</span>
						)}
					</span>
				) : (
					<span />
				)}
			</AppLink>
		</li>
	);
}

/** An in-flight catalog import, shown at the top of the list until it lands. */
function PendingRow({ pending }: { pending: PendingImport }) {
	return (
		<li className={cn(ROW_CLASS, 'bg-surface-field')}>
			<VendorIcon name={pending.label} vendor={pending.apiId} size="sm" />
			<div className="min-w-0">
				<p className="text-foreground-name truncate text-[13.5px] font-semibold">
					{pending.label}
				</p>
				<StatusText tone="loading" className="mt-0.5">
					Adding…
				</StatusText>
			</div>
			<span />
		</li>
	);
}

/** The slot a dragged catalog row lands in, at the top of the list. */
function DropSlot({ drop }: { drop: DragDropState }) {
	const over = drop.phase === 'over';
	return (
		// The drag hook announces the drop through its own live region.
		<div
			aria-hidden="true"
			className={cn(
				'rounded-field -mx-2 mb-1 flex h-11 items-center gap-2.5 px-3 text-[13px] transition-colors duration-[180ms]',
				over ? 'bg-success/10 text-success' : 'bg-surface-field text-muted-foreground',
			)}
			data-testid="workspace-drop-slot"
			data-over={over || undefined}
		>
			<Plus className="h-3.5 w-3.5 shrink-0" />
			<span className="truncate">Drop to add {drop.name}</span>
		</div>
	);
}

export interface PendingImport {
	apiId: string;
	label: string;
}

interface PanelContentProps {
	digest: WorkspaceDigest;
	/** Catalog imports still settling (from `useImportCatalogApi`). */
	pendingImports: PendingImport[];
	/** Opens the import-your-own-spec dialog. */
	onImportOwn: () => void;
	/**
	 * Opens the Add credential flow in place for a "no credential" API.
	 * Omitted ⇒ those names link to the API hub's form instead.
	 */
	onAddCredential?: (row: WorkspaceDigestRow) => void;
	/** A just-added (usable) credential to confirm at the top of the panel. */
	credentialNotice?: CredentialAddedNotice | null;
	onDismissCredentialNotice?: () => void;
	/** A catalog row being dragged toward the panel; null / omitted when idle. */
	drop?: DragDropState | null;
	/** Catalog api ids (`WorkspaceDigestRow.catalogApiId`) of rows that just landed. */
	justAddedApiIds?: ReadonlySet<string>;
}

/** The transient "Credential added for …" confirmation row. */
function CredentialAddedRow({
	notice,
	onDismiss,
}: {
	notice: CredentialAddedNotice;
	onDismiss?: () => void;
}) {
	return (
		<div
			className="bg-success/10 rounded-field flex items-center gap-2 px-3 py-2"
			data-testid="workspace-panel-credential-added"
		>
			<CheckCircle2 className="text-success h-4 w-4 shrink-0" aria-hidden="true" />
			<p
				className="text-foreground-lighter min-w-0 flex-1 truncate text-[13px]"
				title={`Credential added for ${notice.label}`}
			>
				Credential added for <strong className="font-semibold">{notice.label}</strong>
			</p>
			{onDismiss && (
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={onDismiss}
					className="h-6 w-6 shrink-0"
					aria-label="Dismiss"
					data-testid="workspace-panel-credential-added-dismiss"
				>
					<X className="h-3.5 w-3.5" aria-hidden="true" />
				</Button>
			)}
		</div>
	);
}

/**
 * "Your APIs" label + the list's filter controls. Sticky at the top of the
 * panel's scrolling body, so a long list stays narrowable mid-scroll (its fill
 * follows the panel's, including the drag-over tint, via `--panel-bg`). The
 * text filter and the toggle are client-side over the loaded rows (Filter
 * affordance, not Search); counts appear only once every page answered, and
 * both disable when there's nothing to narrow.
 */
function WorkspaceListControls({
	complete,
	rows,
	q,
	onQChange,
	status,
	onStatusChange,
	resultsLabel,
}: {
	/** Every page of the list answered (counts are only shown then). */
	complete: boolean;
	rows: WorkspaceDigestRow[];
	q: string;
	onQChange: (next: string) => void;
	status: WorkspaceStatusFilter;
	onStatusChange: (next: WorkspaceStatusFilter) => void;
	resultsLabel?: string;
}) {
	const counts = useMemo(
		() => ({
			live: rows.filter((r) => matchesStatus(r, 'live')).length,
			draft: rows.filter((r) => matchesStatus(r, 'draft')).length,
			update: rows.filter((r) => matchesStatus(r, 'update')).length,
		}),
		[rows],
	);
	// Never a "· 0" while loading — counts only once the list is whole.
	const countSuffix = (n: number) => (complete ? ` · ${n}` : '');
	const options: { value: WorkspaceStatusFilter; label: string }[] = [
		{ value: 'all', label: 'All' },
		{ value: 'live', label: `Live${countSuffix(counts.live)}` },
		{ value: 'draft', label: `Draft${countSuffix(counts.draft)}` },
		...(counts.update > 0 || status === 'update'
			? [{ value: 'update' as const, label: `Update available${countSuffix(counts.update)}` }]
			: []),
	];
	const disabled = rows.length === 0;
	return (
		<div
			className="sticky top-0 z-10 -mx-2.5 flex flex-col gap-2 bg-[hsl(var(--panel-bg,var(--card)))] px-2.5 pb-1.5"
			data-testid="workspace-panel-filter"
		>
			<div className="flex items-baseline justify-between gap-2">
				<SectionLabel as="h3">Your APIs</SectionLabel>
				{resultsLabel && (
					<span
						className="text-muted-foreground text-xs"
						data-testid="workspace-panel-filter-results"
					>
						{resultsLabel}
					</span>
				)}
			</div>
			<SearchInput
				value={q}
				onValueChange={onQChange}
				size="sm"
				tone="inset"
				// Keeps the shared control edge: on the panel's tonal fill the
				// field alone was ≈1.1:1 and disappeared.
				field
				icon={<Filter className="h-3.5 w-3.5" />}
				placeholder="Filter by name, vendor or description…"
				aria-label="Filter your APIs"
				disabled={disabled}
			/>
			<SegmentedToggle
				options={options}
				value={status}
				onChange={onStatusChange}
				ariaLabel="Filter by serving state"
				disabled={disabled}
				tone="inset"
				className="w-fit max-w-full"
			/>
		</div>
	);
}

/**
 * The panel's scrolling body — attention, your APIs (with in-flight imports
 * and the drop slot at the top), recent changes (or the empty / loading /
 * error state). Shared verbatim by the docked desktop panel and the mobile
 * bottom sheet.
 */
export function WorkspacePanelBody({
	digest,
	pendingImports,
	onImportOwn,
	onAddCredential,
	credentialNotice,
	onDismissCredentialNotice,
	drop,
	justAddedApiIds,
	className,
}: PanelContentProps & { className?: string }) {
	const stream = useAgentStreamOptional();
	const filter = useWorkspaceListFilter();
	const [recentOpen, setRecentOpen] = useState(false);
	const recentId = useId();
	// "What just happened?" — API-side events (imports, catalog updates,
	// overlay lifecycle) from the shell's one live stream.
	const recent = useMemo(
		() =>
			(stream?.events ?? [])
				.filter((ev) => ev.kind === 'import' || ev.kind === 'catalog')
				.slice(0, RECENT_LIMIT),
		[stream?.events],
	);
	// Most recently imported first (`GET /apis` → `created_at`). What needs
	// attention is already surfaced by the "Needs attention" block, so the
	// list itself stays chronological.
	const rows = useMemo(
		() => [...digest.rows].sort(newestFirst((a, b) => a.title.localeCompare(b.title))),
		[digest.rows],
	);
	const shownRows = useMemo(
		() => rows.filter((row) => matchesStatus(row, filter.status) && matchesText(row, filter.q)),
		[rows, filter.status, filter.q],
	);
	const empty = digest.complete && rows.length === 0 && pendingImports.length === 0;
	// Bound agents are read only for the rows the list shows (the shared
	// reader caps the fan-out either way).
	const agentFigure = useAgentFigures(
		shownRows.map((row) => row.credentials),
		digest.credentialsError,
	);

	return (
		<div className={cn('flex flex-col gap-[18px]', className)}>
			{/* Always-present polite live region, so the confirmation is announced. */}
			<div role="status" aria-live="polite" className="empty:hidden">
				{credentialNotice && (
					<CredentialAddedRow
						notice={credentialNotice}
						onDismiss={onDismissCredentialNotice}
					/>
				)}
			</div>
			{digest.error && !digest.complete ? (
				<ErrorAlert message={digest.error} onRetry={digest.retry} />
			) : digest.isPending ? (
				<div className="space-y-2" aria-busy="true">
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-8 w-full" />
					<Skeleton className="h-8 w-full" />
				</div>
			) : empty ? (
				<div>
					{drop && <DropSlot drop={drop} />}
					<div className="px-1 py-4 text-center" data-testid="workspace-panel-empty">
						<p className="text-foreground-lighter text-sm font-semibold">
							Your workspace is empty
						</p>
						<p className="text-muted-foreground mt-1 text-xs">
							Add an API from the catalog, or import your own OpenAPI spec. Added APIs
							show up here.
						</p>
						<Button variant="tonal" size="xs" className="mt-3" onClick={onImportOwn}>
							<Upload size={14} aria-hidden="true" />
							Import your own API
						</Button>
					</div>
				</div>
			) : (
				<>
					{digest.attention.length > 0 ? (
						<NeedsAttention
							attention={digest.attention}
							onAddCredential={onAddCredential}
						/>
					) : digest.attentionComplete ? (
						<div data-testid="workspace-panel-attention" data-tone="neutral">
							<p
								className="bg-surface-field rounded-field text-foreground-lighter flex h-[38px] items-center gap-2.5 px-3 text-[13.5px]"
								data-testid="workspace-panel-all-good"
							>
								<CheckCircle2 className="text-success h-4 w-4" aria-hidden="true" />
								All good — nothing needs you.
							</p>
						</div>
					) : !digest.attentionSettled ? (
						<div data-testid="workspace-panel-attention">
							<SectionLabel as="h3" className="mb-1.5">
								Needs attention
							</SectionLabel>
							<Skeleton className="h-6 w-full" />
						</div>
					) : null}

					{(rows.length > 0 || pendingImports.length > 0) && (
						<div data-testid="workspace-panel-apis">
							<WorkspaceListControls
								complete={digest.complete}
								rows={rows}
								q={filter.q}
								onQChange={filter.setQ}
								status={filter.status}
								onStatusChange={filter.setStatus}
								resultsLabel={
									filter.active
										? `${shownRows.length} of ${rows.length}`
										: undefined
								}
							/>
							{drop && <DropSlot drop={drop} />}
							{pendingImports.length > 0 && (
								<ul data-testid="workspace-panel-importing" className="space-y-0.5">
									{pendingImports.map((p) => (
										<PendingRow key={p.apiId} pending={p} />
									))}
								</ul>
							)}
							{shownRows.length > 0 ? (
								<ul className="space-y-0.5">
									{shownRows.map((row) => {
										const figure = agentFigure(row.credentials);
										return (
											<ApiRow
												key={row.key}
												row={row}
												agentCount={figure.agentCount}
												agentsAtLeast={figure.agentsAtLeast}
												showUsage={digest.usageAvailable}
												usageExhaustive={digest.usageExhaustive}
												justAdded={
													row.catalogApiId != null &&
													(justAddedApiIds?.has(row.catalogApiId) ??
														false)
												}
											/>
										);
									})}
								</ul>
							) : rows.length > 0 ? (
								<div
									className="px-1 py-4 text-center"
									data-testid="workspace-panel-no-matches"
								>
									<p className="text-muted-foreground text-sm">
										No APIs match this filter.
									</p>
									<Button
										variant="ghost"
										size="xs"
										className="mt-1"
										onClick={filter.clear}
									>
										Clear filter
									</Button>
								</div>
							) : null}
						</div>
					)}

					{recent.length > 0 && (
						<div className="-mt-2" data-testid="workspace-panel-recent">
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setRecentOpen((v) => !v)}
								aria-expanded={recentOpen}
								aria-controls={recentId}
								className="text-foreground-lighter hover:text-foreground h-auto w-full justify-between rounded-md px-0 py-1 text-[13px] font-semibold hover:bg-transparent active:scale-100"
								data-testid="workspace-panel-recent-toggle"
							>
								<span>
									Recent changes{' '}
									<span className="text-foreground-faint font-normal">
										· {recent.length}
									</span>
								</span>
								<ChevronDown
									className={cn(
										'text-muted-foreground h-4 w-4 transition-transform',
										recentOpen && 'rotate-180',
									)}
									aria-hidden="true"
								/>
							</Button>
							<ul id={recentId} hidden={!recentOpen} className="mt-1 space-y-0.5">
								{recent.map((ev) => (
									<StreamEventRow key={ev.id} ev={ev} />
								))}
							</ul>
						</div>
					)}
				</>
			)}
		</div>
	);
}

/** "+ Import your own API" — the panel's footer action (opens the dialog in place). */
export function WorkspacePanelFooterActions({ onImportOwn }: { onImportOwn: () => void }) {
	return (
		<Button
			variant="ghost"
			size="sm"
			onClick={onImportOwn}
			className="text-muted-foreground hover:text-foreground h-auto gap-1.5 px-0 py-0 text-[13px] font-normal hover:bg-transparent active:scale-100"
			data-testid="workspace-panel-import-own"
		>
			<Plus size={14} aria-hidden="true" />
			Import your own API
		</Button>
	);
}

/**
 * "N APIs · X live · Y drafts" beside the title, once the list has answered.
 * A zero part is left out rather than shown as "0 drafts".
 */
export function WorkspaceApiCount({ digest }: { digest: WorkspaceDigest }) {
	const { apis, live, draft } = digest.totals;
	if (!digest.complete || apis === 0) return null;
	const parts = [`${apis} API${apis === 1 ? '' : 's'}`];
	// "All live" needs no breakdown; only a mix (or all-draft) says more.
	if (draft > 0) {
		if (live > 0) parts.push(`${live} live`);
		parts.push(`${draft} draft${draft === 1 ? '' : 's'}`);
	}
	return (
		<span className="text-muted-foreground truncate text-xs" data-testid="workspace-api-count">
			{parts.join(' · ')}
		</span>
	);
}

export interface WorkspaceDockPanelProps extends PanelContentProps {
	className?: string;
	ref?: Ref<HTMLElement>;
}

/**
 * Panel fill per drag phase. The fill is set through `--panel-bg` so the
 * sticky filter bar inside the scrolling body can paint the same colour.
 */
const DRAG_SURFACE: Record<'idle' | DragDropState['phase'], string> = {
	idle: '[--panel-bg:var(--surface-1)]',
	dragging: '[--panel-bg:var(--surface-drop)] shadow-[inset_0_0_0_1px_hsl(var(--primary)/0.22)]',
	over: '[--panel-bg:var(--surface-drop-over)] shadow-[inset_0_0_0_1.5px_hsl(var(--success)/0.5)]',
};

/**
 * The docked desktop panel (≥ xl), beside the catalog. Memoised so a catalog
 * search keystroke doesn't re-render the whole panel. The title and footer
 * are fixed; only the body scrolls (the host sizes the panel to the viewport).
 */
export const WorkspaceDockPanel = memo(function WorkspaceDockPanel({
	digest,
	pendingImports,
	onImportOwn,
	onAddCredential,
	credentialNotice,
	onDismissCredentialNotice,
	drop,
	justAddedApiIds,
	className,
	ref,
}: WorkspaceDockPanelProps) {
	const phase = drop?.phase ?? 'idle';
	return (
		<section
			ref={ref}
			aria-label="Your workspace"
			className={cn(
				'rounded-panel flex flex-col overflow-hidden bg-[hsl(var(--panel-bg))] pt-[18px] transition-[background-color,box-shadow] duration-[180ms]',
				DRAG_SURFACE[phase],
				className,
			)}
			data-testid="workspace-dock-panel"
			data-drag={phase}
		>
			<div className="mb-3.5 flex min-w-0 shrink-0 items-baseline gap-2 px-[18px]">
				<h2 className="font-heading shrink-0 text-[15.5px] font-bold text-white">
					Your workspace
				</h2>
				<WorkspaceApiCount digest={digest} />
			</div>

			<div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-[18px] pb-3">
				<WorkspacePanelBody
					digest={digest}
					pendingImports={pendingImports}
					onImportOwn={onImportOwn}
					onAddCredential={onAddCredential}
					credentialNotice={credentialNotice}
					onDismissCredentialNotice={onDismissCredentialNotice}
					drop={drop}
					justAddedApiIds={justAddedApiIds}
				/>
			</div>

			<div className="border-hairline mx-[18px] flex shrink-0 items-center border-t pt-3 pb-3">
				<WorkspacePanelFooterActions onImportOwn={onImportOwn} />
			</div>
		</section>
	);
});
