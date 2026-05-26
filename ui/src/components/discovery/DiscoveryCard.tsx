import {
	ChevronDown,
	ChevronRight,
	ChevronUp,
	ExternalLink,
	Loader2,
	Plus,
	Workflow,
} from 'lucide-react';
import { CardStatusPill } from './CardStatusPill';
import { InspectPanel } from './InspectPanel';
import { VendorIcon } from './VendorIcon';
import { CopyButton } from '@/components/ui/CopyButton';
import { MethodBadge } from '@/components/ui/Badge';
import { AppLink } from '@/components/ui/AppLink';

/**
 * Discriminated entity types the Discover surface handles.
 *
 *   api       — an HTTP API provider. Sources: workspace (locally registered)
 *               or directory (in the public catalog, importable). Search-blender
 *               `catalog_api` / `catalog_workflow_source` rows collapse to this
 *               type with `source: directory` — there's no separate "importable"
 *               concept exposed to users because adding a credential silently
 *               imports anyway.
 *   workflow  — an Arazzo multi-step recipe (from /workflows or /search).
 *   endpoint  — a single HTTP operation. Search-only — appears as a top-level
 *               hit because intent queries like "send an email" match endpoints,
 *               not vendors.
 */
export type DiscoveryEntityType = 'api' | 'workflow' | 'endpoint';

/**
 * Source of truth for where the entity lives, in UI vocabulary:
 *
 *   workspace = entity is registered locally in this jentic-mini instance.
 *               Server raw shape calls this `local`; adapters translate.
 *   directory = entity exists in the upstream public Jentic catalog.
 *               Server raw shape calls this `catalog`.
 */
export type DiscoverySource = 'workspace' | 'directory';

/**
 * Minimum required shape for every entity rendered by DiscoveryCard.
 * The `raw` field carries the original server object so each detail
 * panel can reach whatever fields it needs.
 */
export interface DiscoveryEntity {
	id: string;
	type: DiscoveryEntityType;
	source: DiscoverySource;
	/** Human-readable name / title. */
	summary?: string;
	/** Short description. */
	description?: string;
	/** BM25 relevance score (0–1), present on /search results. */
	score?: number;
	/** Parsed HTTP method, only present for `type === 'endpoint'`. */
	method?: string;
	/** Parent API id derived from an endpoint id (`stripe.com` from
	 *  `GET/stripe.com/v1/customers`). Used in the endpoint breadcrumb. */
	apiId?: string;
	/** Whether the API has credentials configured (only for `type === 'api'`). */
	hasCredentials?: boolean;
	/** Whether the API is registered locally (only for `type === 'api'`). */
	registered?: boolean;
	/** Step count (only for `type === 'workflow'`). */
	stepsCount?: number;
	/** APIs involved in a workflow. */
	involvedApis?: string[];
	/**
	 * Directory APIs (`type === 'api'`, `source === 'directory'`) carry this
	 * flag when the public catalog also ships Arazzo workflows for that
	 * vendor. Replaces the old `catalog_workflow_source` row type — the
	 * server folds workflow availability into the API row and the card
	 * renders a small "+ workflows" chip when this is set.
	 */
	hasWorkflows?: boolean;
	/**
	 * Directory APIs only — the GitHub raw-content URL for the catalog's
	 * OpenAPI spec, surfaced from the manifest by `/apis` and `/search` so
	 * the UI can call `POST /import` directly without a follow-up
	 * `GET /catalog/{api_id}` round-trip. May be undefined on rows derived
	 * from older cached responses; the importer falls back to fetching
	 * `getCatalogEntry` first when missing.
	 */
	specUrl?: string;
	/**
	 * Highlight snippet for search-mode results — populated from
	 * `match_snippet` (P2). Contains the matched substring with `\u0001`
	 * delimiters around the span; render via `<MatchSnippet>` so each
	 * marked range becomes a `<mark>` element.
	 */
	matchSnippet?: string | null;
	raw: any;
}

// ── Shared chrome helpers ─────────────────────────────────────────────────────

/** Webapp-style rounded-full pill badge */
function Pill({
	children,
	hue = 'neutral',
	size = 'md',
}: {
	children: React.ReactNode;
	hue?: 'green' | 'yellow' | 'sky' | 'pink' | 'teal' | 'violet' | 'neutral';
	/**
	 * `md` (default) — standard pill, ~22px tall, used in detail panels.
	 * `sm`           — compact pill (~18px tall, tighter padding) used in
	 *                  card footers where multiple pills compete for
	 *                  horizontal space. Without this knob the API card
	 *                  footer wraps to two rows on narrow viewports
	 *                  (May 2026 review).
	 */
	size?: 'sm' | 'md';
}) {
	const hueCls = {
		green: 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20',
		yellow: 'bg-amber-500/10 text-amber-400 ring-amber-500/20',
		sky: 'bg-sky-500/10 text-sky-400 ring-sky-500/20',
		pink: 'bg-pink-500/10 text-pink-400 ring-pink-500/20',
		teal: 'bg-teal-500/10 text-teal-400 ring-teal-500/20',
		violet: 'bg-violet-500/10 text-violet-400 ring-violet-500/20',
		neutral: 'bg-muted text-muted-foreground ring-border',
	}[hue];
	const sizeCls = size === 'sm' ? 'px-2 py-0 text-[11px]' : 'px-2.5 py-0.5 text-xs';
	return (
		<span
			className={`inline-flex shrink-0 items-center gap-1 rounded-full font-medium whitespace-nowrap ring-1 ${sizeCls} ${hueCls}`}
		>
			{children}
		</span>
	);
}

// ── Search relevance helpers (P2) ─────────────────────────────────────────────

/** `\u0001` delimits highlighted spans in `match_snippet` (see backend P2). */
const HIGHLIGHT_SENTINEL = '\u0001';

/**
 * Render a `match_snippet` payload, wrapping each highlighted span in a
 * `<mark>` element. Snippets are short (~80 chars) and come pre-delimited
 * by the backend, so we just split on the sentinel and alternate plain /
 * highlighted segments.
 */
function MatchSnippet({ snippet }: { snippet: string }) {
	const parts = snippet.split(HIGHLIGHT_SENTINEL);
	return (
		<p
			className="text-muted-foreground mt-1 line-clamp-2 text-xs"
			data-testid="discovery-card-match-snippet"
		>
			…
			{parts.map((seg, i) =>
				i % 2 === 1 ? (
					<mark key={i} className="bg-primary/15 text-foreground rounded px-0.5">
						{seg}
					</mark>
				) : (
					<span key={i}>{seg}</span>
				),
			)}
			…
		</p>
	);
}

/**
 * The outer card shell — shared across every entity type.
 *
 * When `onClick` is passed the header row renders as a `<button>` so the whole
 * surface is clickable (used by cards that expand). When omitted the header is
 * a plain `<div>` — used by cards with no expand state (e.g. directory APIs
 * whose primary affordances are inline action buttons rather than expansion).
 *
 * `accentRail` paints a 2px coloured left rail on the card. Used as the
 * primary visual differentiator between workspace cards (emerald) and
 * directory cards (no rail) — see the May 2026 IA review where the
 * "they all look the same" complaint was traced to identical chrome.
 */
function CardShell({
	children,
	expanded,
	onClick,
	accentRail,
	'data-testid': testId,
	'aria-label': ariaLabel,
	'aria-expanded': ariaExpanded,
}: {
	children: React.ReactNode;
	expanded: boolean;
	onClick?: () => void;
	accentRail?: 'emerald' | 'teal';
	'data-testid'?: string;
	'aria-label'?: string;
	'aria-expanded'?: boolean;
}) {
	const Header = onClick ? 'button' : 'div';
	const headerProps = onClick
		? { type: 'button' as const, onClick }
		: ({} as Record<string, never>);

	const railClass =
		accentRail === 'emerald'
			? 'border-l-2 border-l-emerald-500/60'
			: accentRail === 'teal'
				? 'border-l-2 border-l-teal-500/60'
				: '';

	return (
		<div
			data-testid={testId}
			data-accent-rail={accentRail ?? 'none'}
			className={`group border-border bg-card relative flex h-full cursor-pointer flex-col overflow-hidden rounded-xl border transition-all ${railClass} ${
				expanded
					? 'border-primary/50 bg-card/80'
					: 'hover:border-primary/50 hover:bg-card/80'
			}`}
		>
			<Header
				{...headerProps}
				aria-label={ariaLabel}
				aria-expanded={ariaExpanded}
				className={`relative flex w-full flex-1 items-start gap-4 p-5 text-left ${
					onClick ? '' : 'pointer-events-auto'
				}`}
			>
				{children}
			</Header>
		</div>
	);
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
	return expanded ? (
		<ChevronUp className="text-muted-foreground h-4 w-4" aria-hidden="true" />
	) : (
		<ChevronDown className="text-muted-foreground h-4 w-4" aria-hidden="true" />
	);
}

// ── API card ──────────────────────────────────────────────────────────────────

/**
 * API card — same chrome for both workspace and directory now. The whole
 * surface is clickable and `onToggle` is interpreted by the parent as "open
 * the API Detail Sheet for this entity" (see `DiscoverPage.handleToggle`).
 *
 * On directory cards, the inline "Import to workspace" CTA bypasses the
 * sheet and calls `POST /import` directly via the parent-supplied
 * `onImport` callback. Why direct import (not the credential flow):
 * "Import to workspace" promises *one* outcome (registration), and the
 * credential step is a separate intent the user may not have yet —
 * forcing them through it on import was a UX bug (May 2026 review).
 * Credentials are added afterwards from Workspace.
 *
 * `expanded` here means "this is the active sheet target" — used purely to
 * highlight the card border while the sheet is open over it.
 */
function ApiCard({
	entity,
	expanded,
	onToggle,
	onImport,
	importPending,
}: {
	entity: DiscoveryEntity;
	expanded: boolean;
	onToggle: () => void;
	/**
	 * Triggers the direct-import flow for a directory API. Parent owns
	 * the mutation so a single source-of-truth tracks `pendingApiId`
	 * across the whole grid + the open sheet (clicking import inside
	 * the sheet should disable the inline CTA on the card behind it).
	 */
	onImport?: (entity: DiscoveryEntity) => void;
	/** True while `onImport` is processing this card's api_id. */
	importPending?: boolean;
}) {
	const isWorkspace = entity.source === 'workspace';
	const displayName = entity.summary ?? entity.id;
	const githubUrl: string | undefined = entity.raw?._links?.github;
	// Workspace cards get an emerald left rail for quick visual
	// differentiation from directory cards.
	const accentRail: 'emerald' | undefined = isWorkspace ? 'emerald' : undefined;
	const vendorIconClass = isWorkspace ? '' : 'opacity-80 saturate-[0.85]';

	return (
		<CardShell
			expanded={expanded}
			onClick={onToggle}
			accentRail={accentRail}
			data-testid="discovery-card-api"
			aria-label={displayName}
		>
			<div className={vendorIconClass}>
				<VendorIcon name={displayName} vendor={entity.id} />
			</div>

			{/* `h-full flex-col` + `mt-auto` on the pill row pin the tags to the
			    bottom of the (stretched) card. Without this, cards in the same
			    row with different description lengths show their pills at
			    different vertical positions, which the May 2026 screenshot
			    review flagged as visually noisy. */}
			<div className="flex h-full min-w-0 flex-1 flex-col">
				<h3 className="text-foreground truncate leading-tight font-semibold">
					{displayName}
				</h3>
				{entity.description && (
					<p className="text-muted-foreground mt-1 line-clamp-2 text-sm">
						{entity.description}
					</p>
				)}
				{entity.matchSnippet && <MatchSnippet snippet={entity.matchSnippet} />}

				{/* Footer pill row pinned to the bottom via mt-auto */}
				<div
					className="mt-auto flex w-full flex-wrap items-center gap-1.5 pt-2.5"
					data-testid="discovery-card-footer"
				>
					<CardStatusPill source={entity.source} size="sm" />
					{entity.hasWorkflows && (
						<Pill hue="teal" size="sm">
							<Workflow size={11} aria-hidden="true" /> workflows
						</Pill>
					)}
				</div>
			</div>

			<div className="flex shrink-0 items-center gap-1.5 self-center">
				{!isWorkspace ? (
					<>
						<button
							type="button"
							onClick={(e) => {
								e.stopPropagation();
								if (!importPending) onImport?.(entity);
							}}
							disabled={importPending}
							data-testid="discovery-card-import"
							className="bg-primary text-primary-foreground hover:bg-primary/90 disabled:bg-primary/60 inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium shadow-sm transition-all hover:shadow disabled:cursor-progress"
						>
							{importPending ? (
								<>
									<Loader2 size={13} className="animate-spin" />
									Importing…
								</>
							) : (
								<>
									<Plus size={13} />
									Import
								</>
							)}
						</button>
						{githubUrl && (
							<AppLink
								href={githubUrl}
								className="text-muted-foreground hover:bg-muted hover:text-foreground inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors"
								aria-label={`View ${displayName} on GitHub`}
								title="View on GitHub"
								onClick={(e) => e.stopPropagation()}
							>
								<ExternalLink size={13} />
							</AppLink>
						)}
					</>
				) : (
					<ChevronRight className="text-muted-foreground h-4 w-4" aria-hidden="true" />
				)}
			</div>
		</CardShell>
	);
}

// ── Workflow card ─────────────────────────────────────────────────────────────

function WorkflowCard({
	entity,
	expanded,
	onToggle,
}: {
	entity: DiscoveryEntity;
	expanded: boolean;
	onToggle: () => void;
	onImport?: (entity: DiscoveryEntity) => void;
	importPending?: boolean;
}) {
	const displayName = entity.summary ?? entity.id;

	return (
		<CardShell
			expanded={expanded}
			onClick={onToggle}
			accentRail="teal"
			data-testid="discovery-card-workflow"
			aria-label={displayName}
		>
			{/* Distinctive teal-tinted Workflow icon — different shape language
			    from the VendorIcon used by API cards so the two never blur. */}
			<div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-teal-500/12 ring-1 ring-teal-500/25">
				<Workflow size={18} className="text-teal-400" aria-hidden="true" />
			</div>

			{/* `h-full flex-col` + `mt-auto` keeps pill row pinned to the bottom
			    so workflow cards line up vertically with API cards in a mixed
			    grid row. See ApiCard for the original rationale. */}
			<div className="flex h-full min-w-0 flex-1 flex-col">
				<h3 className="text-foreground truncate leading-tight font-semibold">
					{displayName}
				</h3>
				{entity.description && (
					<p className="text-muted-foreground mt-1 line-clamp-2 text-sm">
						{entity.description}
					</p>
				)}
				{entity.matchSnippet && <MatchSnippet snippet={entity.matchSnippet} />}

				<div className="mt-auto flex flex-wrap items-center gap-1.5 pt-3">
					<Pill hue="teal" size="sm">
						Workflow
					</Pill>
					{entity.stepsCount != null && entity.stepsCount > 0 && (
						<Pill hue="neutral" size="sm">
							{entity.stepsCount} steps
						</Pill>
					)}
					{entity.involvedApis?.slice(0, 3).map((apiId) => (
						<Pill key={apiId} hue="neutral" size="sm">
							{apiId}
						</Pill>
					))}
					{(entity.involvedApis?.length ?? 0) > 3 && (
						<span className="text-muted-foreground text-xs">
							+{(entity.involvedApis?.length ?? 0) - 3}
						</span>
					)}
				</div>
			</div>

			<div className="shrink-0 pt-0.5">
				<ChevronIcon expanded={expanded} />
			</div>
		</CardShell>
	);
}

// ── Endpoint card (search-only) ───────────────────────────────────────────────

function EndpointCard({
	entity,
	expanded,
	onToggle,
}: {
	entity: DiscoveryEntity;
	expanded: boolean;
	onToggle: () => void;
	onImport?: (entity: DiscoveryEntity) => void;
	importPending?: boolean;
}) {
	const isWorkspace = entity.source === 'workspace';

	return (
		<CardShell
			expanded={expanded}
			onClick={onToggle}
			accentRail={isWorkspace ? 'emerald' : undefined}
			data-testid="discovery-card-endpoint"
			aria-label={entity.summary ?? entity.id}
			aria-expanded={expanded}
		>
			<div className="flex h-10 w-10 shrink-0 items-center justify-center">
				{entity.method ? (
					<MethodBadge method={entity.method} />
				) : (
					<VendorIcon
						name={entity.apiId ?? entity.id}
						vendor={entity.apiId ?? entity.id}
						size="sm"
					/>
				)}
			</div>

			{/* `h-full flex-col` + `mt-auto` keeps pill row pinned to the bottom
			    so mixed grids (api + workflow + endpoint) align horizontally. */}
			<div className="flex h-full min-w-0 flex-1 flex-col">
				{/* Parent-API breadcrumb makes the "this is a single HTTP call
				    inside API X" relationship explicit. */}
				{entity.apiId && (
					<div className="text-muted-foreground mb-0.5 flex items-center gap-1 text-xs">
						<span className="font-mono">{entity.apiId}</span>
						<ChevronRight className="h-3 w-3 opacity-50" aria-hidden="true" />
					</div>
				)}
				<h3 className="text-foreground truncate leading-tight font-semibold">
					{entity.summary ?? entity.id}
				</h3>
				<div className="mt-0.5 flex items-center gap-1.5">
					<code className="text-muted-foreground max-w-full truncate font-mono text-xs">
						{entity.id}
					</code>
					<CopyButton value={entity.id} />
				</div>
				{entity.description && (
					<p className="text-muted-foreground mt-1 line-clamp-2 text-sm">
						{entity.description}
					</p>
				)}
				{entity.matchSnippet && <MatchSnippet snippet={entity.matchSnippet} />}

				<div className="mt-auto flex flex-wrap items-center gap-1.5 pt-3">
					<Pill hue="sky" size="sm">
						Endpoint
					</Pill>
					<CardStatusPill source={entity.source} size="sm" />
					{entity.score != null && entity.score > 0 && (
						<Pill hue="neutral" size="sm">
							{Math.round(entity.score * 100)}% match
						</Pill>
					)}
				</div>
			</div>

			<div className="shrink-0 pt-0.5">
				<ChevronIcon expanded={expanded} />
			</div>
		</CardShell>
	);
}

// ── Expanded panels ───────────────────────────────────────────────────────────
//
// Endpoint cards still expand inline (search-mode drill-down for raw HTTP
// operations). API and workflow cards do NOT — they open dedicated detail
// sheets instead (parent handles routing on click). The `?inspect=` and
// `?inspect_wf=` URL params drive the respective sheets.

function ExpandedPanel({ entity, onClose }: { entity: DiscoveryEntity; onClose: () => void }) {
	if (entity.type === 'endpoint') {
		return <InspectPanel capabilityId={entity.id} onClose={onClose} />;
	}

	// `api` and `workflow` reach this code path only if the caller incorrectly
	// toggled inline expansion for those entity types — both have their own
	// sheets (`ApiDetailSheet` / `WorkflowDetailSheet`). Render nothing so
	// we fail closed rather than show inconsistent UI alongside the sheet.
	return null;
}

// ── Public component ──────────────────────────────────────────────────────────

const CARD_BY_TYPE: Record<
	DiscoveryEntityType,
	(p: {
		entity: DiscoveryEntity;
		expanded: boolean;
		onToggle: () => void;
		onImport?: (entity: DiscoveryEntity) => void;
		importPending?: boolean;
	}) => JSX.Element
> = {
	api: ApiCard,
	workflow: WorkflowCard,
	endpoint: EndpointCard,
};

/**
 * Polymorphic discovery row. Picks the right sub-card by `entity.type` and —
 * for endpoints only — wraps it in the inline expand chrome.
 *
 * For API and workflow entities, `expanded=true` only highlights the card
 * border (because the corresponding detail sheet is open over it). No inline
 * expansion is rendered — those types route to `ApiDetailSheet` /
 * `WorkflowDetailSheet` via the parent's `?inspect=` / `?inspect_wf=` params.
 *
 * Card chrome differs per type intentionally so the three kinds (API,
 * Workflow, Endpoint) can be distinguished at a glance.
 *
 * `onImport` / `importPending` flow only matters for `entity.type === 'api'`
 * with `source === 'directory'`; the other sub-cards ignore them.
 */
export function DiscoveryCard({
	entity,
	expanded,
	onToggle,
	onImport,
	importPending,
}: {
	entity: DiscoveryEntity;
	expanded: boolean;
	onToggle: () => void;
	onImport?: (entity: DiscoveryEntity) => void;
	importPending?: boolean;
}) {
	const SubCard = CARD_BY_TYPE[entity.type];
	const showInlineExpansion = expanded && entity.type === 'endpoint';

	return (
		<div className="overflow-hidden rounded-xl">
			<SubCard
				entity={entity}
				expanded={expanded}
				onToggle={onToggle}
				onImport={onImport}
				importPending={importPending}
			/>
			{showInlineExpansion && (
				<div className="bg-card border-border/60 overflow-hidden rounded-b-xl border border-t-0">
					<ExpandedPanel entity={entity} onClose={onToggle} />
				</div>
			)}
		</div>
	);
}
