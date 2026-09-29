/**
 * WorkspaceDockPanel — the "Your workspace" card docked beside the Library
 * catalog (sticky on xl, like Monitor's Live activity panel). High-level and
 * human: each block answers one question, in the order an operator asks it.
 *
 *   1. Does anything need me?   — only non-zero attention items, else "All good"
 *   2. What's importing?        — the catalog's own in-flight imports (transient)
 *   3. What's in my workspace?  — compact list: state, agents with access, 7-day line
 *   4. What just happened?      — API events off the shell's live stream
 *
 * Every figure comes from {@link useWorkspaceDigest} (real registry, credential
 * and usage reads) or the live event stream; a signal whose read hasn't
 * answered — or isn't readable for this user — is omitted, never zeroed.
 *
 * Expand (and the footer link) is an ordinary link to the full Workspace view;
 * both surfaces carry the `library-workspace` view-transition name, so the
 * shell's link transitions morph this card into the page.
 */
import { memo, useMemo } from 'react';
import {
	AlertTriangle,
	ArrowRight,
	Bot,
	CheckCircle2,
	FileClock,
	GitPullRequestArrow,
	KeyRound,
	Loader2,
	Maximize2,
	Plus,
	RefreshCw,
	Upload,
} from 'lucide-react';
import {
	ApiStateBadges,
	AppLink,
	Button,
	Card,
	CardBody,
	CardFooter,
	CardHeader,
	CardTitle,
	ErrorAlert,
	Skeleton,
	ApiUsageSummary,
	StreamEventRow,
	VendorIcon,
} from '@/shared/ui';
import { callsInWeek, useAgentFigures } from '@/shared/credentials/api/apiHealth';
import { ROUTES, ROUTE_PATHS } from '@/shared/app/routes';
import { libraryWorkspaceVtStyle } from '@/shared/app/viewTransitions';
import { useAgentStreamOptional } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { newestFirst } from '@/shared/lib/newestFirst';
import type {
	AttentionEntry,
	AttentionId,
	WorkspaceDigest,
	WorkspaceDigestRow,
} from '@/modules/discover/api';

const LIST_LIMIT = 8;
const RECENT_LIMIT = 5;

const ATTENTION_ICON: Record<AttentionId, typeof AlertTriangle> = {
	updates: RefreshCw,
	overlays: GitPullRequestArrow,
	failures: AlertTriangle,
	credentials: KeyRound,
	drafts: FileClock,
};

const ATTENTION_TONE: Record<AttentionId, string> = {
	updates: 'text-warning',
	overlays: 'text-primary',
	failures: 'text-danger',
	credentials: 'text-accent-orange',
	drafts: 'text-muted-foreground',
};

/** Workspace-view filter each attention kind maps onto, when one exists. */
const ATTENTION_FILTER: Partial<Record<AttentionId, string>> = {
	updates: 'update',
	drafts: 'draft',
};

/** Eyebrow caption above a block of the panel (a label, not a heading). */
function SectionLabel({ children }: { children: React.ReactNode }) {
	return (
		<p className="text-muted-foreground mb-1.5 px-1 text-xs tracking-wider uppercase">
			{children}
		</p>
	);
}

function hrefFor(row: WorkspaceDigestRow, entry: AttentionEntry): string {
	// "No credential yet" names one API per link, so each opens that API's hub
	// straight onto its Add credential form rather than the generic picker.
	return ROUTE_PATHS.workspaceApiHub(row.ref, entry.tab, {
		addCredential: entry.id === 'credentials',
	});
}

function AttentionItem({ entry }: { entry: AttentionEntry }) {
	const Icon = ATTENTION_ICON[entry.id];
	const count = entry.rows.length;
	const filter = ATTENTION_FILTER[entry.id];
	const shown = entry.rows.slice(0, 3);
	const rest = count - shown.length;
	const noun = count === 1 ? 'API' : 'APIs';
	return (
		<li className="flex items-start gap-2.5 px-1 py-1.5" data-testid={`attention-${entry.id}`}>
			<Icon
				className={`mt-0.5 h-4 w-4 shrink-0 ${ATTENTION_TONE[entry.id]}`}
				aria-hidden="true"
			/>
			<div className="min-w-0 flex-1">
				<p className="text-foreground text-sm">
					<strong className="font-semibold">
						{count}
						{entry.atLeast ? '+' : ''}
					</strong>{' '}
					{noun} · {entry.label}
				</p>
				<p className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs">
					{shown.map((row) => (
						<AppLink
							key={row.key}
							href={hrefFor(row, entry)}
							className="text-primary truncate hover:underline"
						>
							{row.title}
							{entry.id === 'failures' && row.usage ? ` (${row.usage.failed})` : ''}
						</AppLink>
					))}
					{rest > 0 && (
						<AppLink
							href={
								filter ? `${ROUTES.workspace}?status=${filter}` : ROUTES.workspace
							}
							className="text-muted-foreground hover:text-foreground"
						>
							+{rest} more
						</AppLink>
					)}
				</p>
			</div>
		</li>
	);
}

function ApiRow({
	row,
	agentCount,
	showUsage,
	usageExhaustive,
}: {
	row: WorkspaceDigestRow;
	/** Agents with access; null while loading or unknowable (then omitted). */
	agentCount: number | null;
	showUsage: boolean;
	usageExhaustive: boolean;
}) {
	const calls = callsInWeek(row.usage, usageExhaustive);
	return (
		<li>
			<AppLink
				href={row.href}
				className="hover:bg-muted/60 flex items-center gap-2.5 rounded-lg px-1.5 py-1.5 transition-colors"
				data-testid="workspace-panel-api"
			>
				<VendorIcon
					name={row.title}
					vendor={row.host ?? row.ref.vendor}
					iconUrl={row.iconUrl}
					size="sm"
				/>
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-1.5">
						<span className="text-foreground truncate text-sm font-medium">
							{row.title}
						</span>
					</div>
					<div className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-[11px]">
						<ApiStateBadges
							currentRevisionId={row.currentRevisionId}
							updateAvailable={row.updateAvailable}
							short
							className="px-1.5 py-0 text-[10px]"
						/>
						{agentCount != null && (
							<span
								className="inline-flex items-center gap-0.5"
								title="Agents bound to a credential for this API"
							>
								<Bot className="h-3 w-3" aria-hidden="true" />
								{agentCount}
							</span>
						)}
					</div>
				</div>
				{showUsage && calls != null && (
					<ApiUsageSummary size="compact" calls={calls} trend={row.usage?.trend} />
				)}
			</AppLink>
		</li>
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
}

/**
 * The panel's scrolling body — attention, importing, your APIs, recent changes
 * (or the empty / loading / error state). Shared verbatim by the docked
 * desktop card and the mobile bottom sheet.
 */
export function WorkspacePanelBody({
	digest,
	pendingImports,
	onImportOwn,
	className,
}: PanelContentProps & { className?: string }) {
	const stream = useAgentStreamOptional();
	// "What just happened?" — API-side events (imports, catalog updates,
	// overlay lifecycle) from the shell's one live stream.
	const recent = useMemo(
		() =>
			(stream?.events ?? [])
				.filter((ev) => ev.kind === 'import' || ev.kind === 'catalog')
				.slice(0, RECENT_LIMIT),
		[stream?.events],
	);
	// Most recently imported first (`GET /apis` → `created_at`), matching the
	// expanded Workspace grid. What needs attention is already surfaced by the
	// "Needs attention" block, so the list itself stays chronological.
	const rows = useMemo(
		() => [...digest.rows].sort(newestFirst((a, b) => a.title.localeCompare(b.title))),
		[digest.rows],
	);
	const empty = digest.complete && rows.length === 0 && pendingImports.length === 0;
	// Bound agents are read only for the rows the list shows.
	const shownRows = rows.slice(0, LIST_LIMIT);
	const agentFigure = useAgentFigures(
		shownRows.map((row) => row.credentials),
		digest.credentialsError,
	);

	return (
		<div className={cn('space-y-4', className)}>
			{digest.error && !digest.complete ? (
				<ErrorAlert message={digest.error} onRetry={digest.retry} />
			) : digest.isPending ? (
				<div className="space-y-2" aria-busy="true">
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-8 w-full" />
					<Skeleton className="h-8 w-full" />
				</div>
			) : empty ? (
				<div className="px-1 py-4 text-center" data-testid="workspace-panel-empty">
					<p className="text-foreground text-sm font-medium">Your workspace is empty</p>
					<p className="text-muted-foreground mt-1 text-xs">
						Add an API from the catalog, or import your own OpenAPI spec. Added APIs
						show up here.
					</p>
					<Button variant="outline" size="sm" className="mt-3" onClick={onImportOwn}>
						<Upload size={14} aria-hidden="true" />
						Import your own API
					</Button>
				</div>
			) : (
				<>
					{digest.attention.length > 0 ? (
						<div
							className="border-warning/30 bg-warning/10 rounded-lg border px-2 pt-2 pb-1"
							data-testid="workspace-panel-attention"
							data-tone="warning"
						>
							<p className="text-warning mb-1 flex items-center gap-1.5 px-1 text-xs font-medium tracking-wider uppercase">
								<AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
								Needs attention
							</p>
							<ul className="divide-warning/15 divide-y">
								{digest.attention.map((entry) => (
									<AttentionItem key={entry.id} entry={entry} />
								))}
							</ul>
						</div>
					) : digest.attentionComplete ? (
						<div
							className="border-border/60 bg-muted/30 rounded-lg border px-2 py-2"
							data-testid="workspace-panel-attention"
							data-tone="neutral"
						>
							<p
								className="text-muted-foreground flex items-center gap-1.5 px-1 text-sm"
								data-testid="workspace-panel-all-good"
							>
								<CheckCircle2 className="text-success h-4 w-4" aria-hidden="true" />
								All good — nothing needs you.
							</p>
						</div>
					) : !digest.attentionSettled ? (
						<div data-testid="workspace-panel-attention">
							<SectionLabel>Needs attention</SectionLabel>
							<Skeleton className="h-6 w-full" />
						</div>
					) : null}

					{pendingImports.length > 0 && (
						<div data-testid="workspace-panel-importing">
							<SectionLabel>Adding</SectionLabel>
							<ul className="space-y-1">
								{pendingImports.map((p) => (
									<li
										key={p.apiId}
										className="bg-primary/5 text-foreground flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm"
									>
										<Loader2
											className="text-primary h-3.5 w-3.5 animate-spin"
											aria-hidden="true"
										/>
										<span className="min-w-0 flex-1 truncate">{p.label}</span>
										<span className="text-muted-foreground text-[11px]">
											Adding…
										</span>
									</li>
								))}
							</ul>
						</div>
					)}

					{rows.length > 0 && (
						<div>
							<SectionLabel>Your APIs</SectionLabel>
							<ul className="space-y-0.5">
								{shownRows.map((row) => (
									<ApiRow
										key={row.key}
										row={row}
										agentCount={agentFigure(row.credentials).agentCount}
										showUsage={digest.usageAvailable}
										usageExhaustive={digest.usageExhaustive}
									/>
								))}
							</ul>
							{rows.length > LIST_LIMIT && (
								<AppLink
									href={ROUTES.workspace}
									className="text-muted-foreground hover:text-foreground mt-1 block px-1.5 text-xs"
								>
									+{rows.length - LIST_LIMIT} more in your workspace
								</AppLink>
							)}
						</div>
					)}

					{recent.length > 0 && (
						<div data-testid="workspace-panel-recent">
							<SectionLabel>Recent changes</SectionLabel>
							<ul className="space-y-0.5">
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

/** "Import your own API" + "Open your workspace →" — the panel's footer actions. */
export function WorkspacePanelFooterActions({ onImportOwn }: { onImportOwn: () => void }) {
	return (
		<>
			<Button
				variant="ghost"
				size="sm"
				onClick={onImportOwn}
				className="text-muted-foreground -ml-2 h-7 gap-1 px-2 text-xs"
			>
				<Plus size={12} aria-hidden="true" />
				Import your own API
			</Button>
			<AppLink
				href={ROUTES.workspace}
				className="text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline"
				data-testid="workspace-panel-open"
			>
				Open your workspace
				<ArrowRight size={14} aria-hidden="true" />
			</AppLink>
		</>
	);
}

/** "N APIs" beside the title, once the list has answered. */
export function WorkspaceApiCount({ digest }: { digest: WorkspaceDigest }) {
	const count = digest.rows.length;
	if (!digest.complete || count === 0) return null;
	return (
		<span className="text-muted-foreground text-xs">
			{count} API{count === 1 ? '' : 's'}
		</span>
	);
}

export interface WorkspaceDockPanelProps extends PanelContentProps {
	className?: string;
}

/**
 * The docked desktop card (≥ xl), beside the catalog. Memoised so a catalog
 * search keystroke doesn't re-render the whole panel.
 */
export const WorkspaceDockPanel = memo(function WorkspaceDockPanel({
	digest,
	pendingImports,
	onImportOwn,
	className,
}: WorkspaceDockPanelProps) {
	return (
		<section
			aria-label="Your workspace"
			className={className}
			style={libraryWorkspaceVtStyle}
			data-testid="workspace-dock-panel"
		>
			<Card className="flex h-full flex-col">
				<CardHeader className="flex items-center justify-between gap-2 py-3">
					<div className="flex min-w-0 items-baseline gap-2">
						<CardTitle as="h2" className="text-base">
							Your workspace
						</CardTitle>
						<WorkspaceApiCount digest={digest} />
					</div>
					<AppLink
						href={ROUTES.workspace}
						variant="ghost"
						size="sm"
						className="h-8 w-8 p-0"
						aria-label="Expand to the full workspace"
						title="Expand to the full workspace"
						data-testid="workspace-panel-expand"
					>
						<Maximize2 className="h-4 w-4" aria-hidden="true" />
					</AppLink>
				</CardHeader>

				<CardBody className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
					<WorkspacePanelBody
						digest={digest}
						pendingImports={pendingImports}
						onImportOwn={onImportOwn}
					/>
				</CardBody>

				<CardFooter className="flex items-center justify-between gap-2 py-2">
					<WorkspacePanelFooterActions onImportOwn={onImportOwn} />
				</CardFooter>
			</Card>
		</section>
	);
});
