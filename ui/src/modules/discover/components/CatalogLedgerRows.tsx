/**
 * CatalogLedgerRows — the Library ledger's row kinds: an API row, a vendor
 * header (2–5 APIs, or an expanded big vendor), and a collapsed big-vendor
 * summary row ("vendor · A, B, C, D +N more"). Presentational; the ledger
 * owns state and hands each row its facts.
 *
 * Keyboard: no custom shortcuts — each row's controls are real buttons/links
 * in the Tab order (the API name previews, then GitHub · Add / Open).
 *
 * Tree lines: a vendor header draws a short stub down from under its avatar;
 * each child draws a vertical through its top half + an elbow into its own
 * avatar's centre, and (unless it's the last child) a vertical through its
 * whole height to reach the next child. The child's cell spans the full row
 * height, so the geometry is exact in row pixels and never crosses an avatar.
 */
import { memo, type PointerEvent as ReactPointerEvent, type ReactNode, type Ref } from 'react';
import { ChevronDown, ChevronRight, ChevronUp, GripVertical, KeyRound, Plus } from 'lucide-react';
import {
	AppLink,
	Button,
	GitHubMark,
	LedgerRow,
	LedgerRowActions,
	StatusText,
	Tooltip,
	VendorIcon,
} from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { cn } from '@/shared/lib/utils';
import type { Credential } from '@/shared/credentials/api';
import type { DiscoveryEntity } from '@/modules/discover/api';
import {
	formatVendorCount,
	matchRanges,
	type VendorCount,
} from '@/modules/discover/lib/catalogGroups';

const CREDENTIAL_READY_HINT = 'You already have a credential that covers this API';

/*
 * Tree geometry (row = 38px, avatar = 24px, so an avatar spans y 7–31 and is
 * centred at x 12 of its cell). Lines sit at x 11–12, behind nothing.
 */
/** Child cell: full row height; elbow from the top into the avatar's centre. */
const ELBOW =
	"relative self-stretch pl-[34px] before:pointer-events-none before:absolute before:top-0 before:left-[11px] before:h-1/2 before:w-[17px] before:rounded-bl-md before:border-b before:border-l before:border-ledger-tree before:content-['']";
/** Not the last child: the line carries on through the row to the next child. */
const ELBOW_CONTINUES =
	"after:pointer-events-none after:absolute after:inset-y-0 after:left-[11px] after:border-l after:border-ledger-tree after:content-['']";
/** Vendor header: a stub from 3px under its avatar down to the row's bottom. */
const HEADER_STUB =
	"relative self-stretch after:pointer-events-none after:absolute after:top-[34px] after:bottom-0 after:left-[11px] after:border-l after:border-ledger-tree after:content-['']";

const NAME = 'font-heading text-foreground-name min-w-0 truncate text-[13.5px]';

/** Text with every case-insensitive occurrence of `query` marked. */
function Highlight({ text, query }: { text: string; query?: string }) {
	const ranges = query ? matchRanges(text, query) : [];
	if (ranges.length === 0) return <>{text}</>;
	const out: ReactNode[] = [];
	let at = 0;
	ranges.forEach(([start, end], i) => {
		if (start > at) out.push(text.slice(at, start));
		out.push(
			<mark key={i} className="bg-primary/20 text-search-highlight rounded-[3px]">
				{text.slice(start, end)}
			</mark>,
		);
		at = end;
	});
	if (at < text.length) out.push(text.slice(at));
	return <>{out}</>;
}

export interface CatalogApiRowProps {
	entity: DiscoveryEntity;
	/** Display title (the workspace API's when exactly one is matched). */
	title: string;
	icon: { name: string; vendor?: string; iconUrl?: string | null };
	/** Vendor domain shown in the Vendor column (blank when it repeats the title). */
	vendorLabel: string;
	versionLabel: string | null;
	/** Flat search layout: vendor + version inline, with match highlight. */
	flat: boolean;
	query?: string;
	child: boolean;
	/** A child that isn't its vendor's last: the tree line continues below. */
	childContinues?: boolean;
	zebra: boolean;
	selected: boolean;
	pending: boolean;
	/** Hub link for imported rows; review-update link when an update is waiting. */
	openHref: string | null;
	openLabel: string;
	reviewHref: string | null;
	readyCredentials: Credential[] | null;
	dragging: boolean;
	onOpen: (entity: DiscoveryEntity) => void;
	onImport: (entity: DiscoveryEntity) => void;
	onPointerDown?: (event: ReactPointerEvent<HTMLElement>, entity: DiscoveryEntity) => void;
	/** True when the click ended a drag (so it shouldn't open the preview). */
	consumeDragClick?: () => boolean;
	/** Phones: the status goes under the name instead of in its own column. */
	stackStatus?: boolean;
}

function RowStatus({ entity, pending }: { entity: DiscoveryEntity; pending: boolean }) {
	if (pending) {
		return (
			<StatusText tone="loading" data-testid="catalog-status-pending">
				Adding…
			</StatusText>
		);
	}
	if (!entity.registered) return null;
	return entity.updateAvailable ? (
		<StatusText tone="caution" data-testid="catalog-status-update">
			Update available
		</StatusText>
	) : (
		<StatusText tone="success" data-testid="catalog-status-imported">
			In your workspace
		</StatusText>
	);
}

export const CatalogApiRow = memo(function CatalogApiRow({
	entity,
	title,
	icon,
	vendorLabel,
	versionLabel,
	flat,
	query,
	child,
	childContinues = false,
	zebra,
	selected,
	pending,
	openHref,
	openLabel,
	reviewHref,
	readyCredentials,
	dragging,
	onOpen,
	onImport,
	onPointerDown,
	consumeDragClick,
	stackStatus = false,
}: CatalogApiRowProps) {
	const canAdd = !entity.registered && !pending;
	const credentials = canAdd && readyCredentials?.length ? readyCredentials : null;
	const draggable = canAdd && onPointerDown != null;
	// The row's state, in one place: the Status column on wider screens, a line
	// under the name on phones (where a column would sit under the actions).
	const hasStatus = pending || entity.registered || credentials != null;
	const status = hasStatus ? (
		<>
			<RowStatus entity={entity} pending={pending} />
			{credentials && <CredentialReadyLink credentials={credentials} />}
		</>
	) : null;

	return (
		<LedgerRow
			data-testid="catalog-row"
			data-api-id={entity.apiId}
			data-registered={entity.registered}
			zebra={zebra}
			selected={selected}
			onClick={() => {
				if (consumeDragClick?.()) return;
				onOpen(entity);
			}}
			onPointerDown={draggable ? (e) => onPointerDown(e, entity) : undefined}
			className={cn(
				draggable && 'cursor-grab select-none active:cursor-grabbing',
				dragging && 'opacity-35',
				stackStatus && hasStatus && 'h-auto min-h-[38px] py-1',
			)}
			actions={
				<LedgerRowActions>
					{entity.githubUrl && (
						<Tooltip content="View spec on GitHub" interactiveChild>
							<AppLink
								href={entity.githubUrl}
								variant="tonal"
								size="icon-xs"
								aria-label={`View ${title} spec on GitHub`}
								data-testid="catalog-row-github"
							>
								<GitHubMark />
							</AppLink>
						</Tooltip>
					)}
					{entity.registered ? (
						<>
							{reviewHref && (
								<AppLink
									href={reviewHref}
									variant="tonal"
									size="xs"
									aria-label={`Review the update to ${title}`}
									data-testid="catalog-row-review-update"
								>
									{/* Phones keep it short, so the status beside it still fits. */}
									Review
									<span className={cn(stackStatus && 'hidden')}> update</span> →
								</AppLink>
							)}
							{openHref && !reviewHref && (
								<AppLink
									href={openHref}
									variant="tonal"
									size="xs"
									aria-label={openLabel}
									data-testid="catalog-row-open"
								>
									Open →
								</AppLink>
							)}
						</>
					) : (
						// While it's adding, the row's status says so — no second
						// "Adding…" on the button.
						!pending && (
							<Tooltip content="Add to workspace" interactiveChild>
								<Button
									variant="tonal"
									size="xs"
									onClick={(e) => {
										e.stopPropagation();
										onImport(entity);
									}}
									aria-label={`Add ${title} to workspace`}
									data-testid="catalog-row-add"
								>
									<Plus size={14} aria-hidden="true" />
									<span className="[@media(hover:none)]:sr-only">Add</span>
								</Button>
							</Tooltip>
						)
					)}
				</LedgerRowActions>
			}
		>
			<div
				role="cell"
				className={cn(
					'relative flex min-w-0 items-center gap-2.5',
					child && ELBOW,
					child && childContinues && ELBOW_CONTINUES,
				)}
			>
				{draggable && (
					// Hover-only drag affordance (the cursor turns to a grab hand too).
					<GripVertical
						size={10}
						aria-hidden="true"
						data-testid="catalog-row-grip"
						className="text-foreground-faint pointer-events-none absolute top-1/2 -left-[9px] -translate-y-1/2 opacity-0 transition-opacity duration-[140ms] group-hover/row:opacity-70 [@media(hover:none)]:hidden"
					/>
				)}
				<VendorIcon {...icon} size="xs" />
				{/* Phones stack the name over the row's status, clear of the actions
				    pinned on the right. */}
				<div
					className={cn(
						'flex min-w-0 items-center gap-2.5',
						// Clear of the pinned actions (a little wider beside "Review →").
						stackStatus && 'flex-col items-start gap-0',
						stackStatus && (reviewHref ? 'pr-32' : 'pr-28'),
					)}
				>
					{/* The row's keyboard target: Tab here, Enter/Space previews. */}
					<Button
						variant="ghost"
						size="xs"
						className={cn(
							NAME,
							child ? 'font-medium' : 'font-semibold',
							'block h-auto max-w-full cursor-[inherit] rounded-[4px] p-0 text-left hover:bg-transparent active:scale-100',
							'hover:text-foreground-name focus-visible:ring-offset-0',
						)}
						title={title}
						aria-label={`View ${title}`}
						data-testid="catalog-row-open-preview"
						onClick={(e) => {
							e.stopPropagation();
							if (consumeDragClick?.()) return;
							onOpen(entity);
						}}
					>
						<Highlight text={title} query={flat ? query : undefined} />
					</Button>
					{flat && (vendorLabel || versionLabel) && (
						<span className="text-muted-foreground max-w-full min-w-0 shrink truncate text-[12.5px]">
							{vendorLabel && <Highlight text={vendorLabel} query={query} />}
							{vendorLabel && versionLabel && <span aria-hidden="true"> · </span>}
							{versionLabel && (
								<span className="font-mono text-[11.5px]">{versionLabel}</span>
							)}
						</span>
					)}
					{stackStatus && status && (
						<span
							className="flex max-w-full min-w-0 items-center gap-2"
							data-testid="catalog-row-status-stacked"
						>
							{status}
						</span>
					)}
				</div>
			</div>
			{!flat && (
				<>
					<span
						role="cell"
						className="text-muted-foreground hidden truncate text-[12.5px] sm:block"
						data-testid="catalog-row-vendor"
					>
						{vendorLabel}
					</span>
					<span
						role="cell"
						className="text-muted-foreground hidden truncate font-mono text-[11.5px] sm:block"
						data-testid="catalog-row-version"
					>
						{versionLabel}
					</span>
				</>
			)}
			<span role="cell" className="flex min-w-0 items-center">
				{!stackStatus && status}
			</span>
		</LedgerRow>
	);
});

/**
 * "Credential ready" — an existing credential already covers this entry. No
 * stacking context of its own, so the row's hover actions (painted later,
 * positioned) sit above it and stay clickable; while they're hidden they
 * don't take pointer events, so the chip's link works.
 */
function CredentialReadyLink({ credentials }: { credentials: Credential[] }) {
	return (
		<AppLink
			href={ROUTE_PATHS.credentialInventory()}
			data-nodrag=""
			className="text-success inline-flex min-w-0 items-center gap-1 truncate text-xs font-bold hover:underline"
			data-testid="catalog-row-credential-ready"
			title={`${CREDENTIAL_READY_HINT}: ${credentials.map((c) => c.name).join(', ')}`}
			aria-label={`Credential ready. ${CREDENTIAL_READY_HINT}. Opens the Credentials list on the Agents page.`}
			onClick={(e) => e.stopPropagation()}
		>
			<KeyRound size={12} aria-hidden="true" className="shrink-0" />
			<span className="truncate">Credential ready</span>
		</AppLink>
	);
}

/** Vendor header (2–5 APIs, or an expanded big vendor — then it collapses back). */
export function CatalogVendorRow({
	vendor,
	total,
	zebra,
	collapsible,
	onCollapse,
}: {
	vendor: string;
	total: VendorCount;
	zebra: boolean;
	collapsible: boolean;
	onCollapse: () => void;
}) {
	return (
		<LedgerRow
			data-testid="catalog-vendor-row"
			data-vendor={vendor}
			zebra={zebra}
			interactive={collapsible}
			onActivate={collapsible ? onCollapse : undefined}
		>
			<div role="cell" className={cn('flex min-w-0 items-center gap-2.5', HEADER_STUB)}>
				<VendorIcon name={vendor} vendor={vendor} size="xs" />
				<span className={cn(NAME, 'font-semibold')}>{vendor}</span>
				<span className="text-foreground-faint shrink-0 text-xs">
					{formatVendorCount(total)}
				</span>
			</div>
			<span
				role="cell"
				className="text-muted-foreground hidden truncate text-[12.5px] sm:block"
			>
				{vendor}
			</span>
			<span role="cell" className="hidden sm:block" />
			<span role="cell" className="flex items-center">
				{collapsible && (
					<Button
						variant="ghost"
						size="xs"
						aria-expanded={true}
						aria-label={`Show fewer ${vendor} APIs`}
						onClick={(e) => {
							e.stopPropagation();
							onCollapse();
						}}
						className="-ml-2 px-2"
						data-testid="catalog-vendor-toggle"
					>
						<ChevronDown size={14} aria-hidden="true" />
						Show fewer
					</Button>
				)}
			</span>
		</LedgerRow>
	);
}

/** A big vendor folded into one row; click (or its Show all button) expands it in place. */
export function CatalogVendorSummaryRow({
	vendor,
	total,
	names,
	more,
	zebra,
	onExpand,
}: {
	vendor: string;
	total: VendorCount;
	names: string[];
	more: number;
	zebra: boolean;
	onExpand: () => void;
}) {
	const moreText = more > 0 ? ` +${more}${total.atLeast ? '+' : ''} more` : '';
	return (
		<LedgerRow
			data-testid="catalog-vendor-summary"
			data-vendor={vendor}
			zebra={zebra}
			onActivate={onExpand}
		>
			<div role="cell" className="flex min-w-0 items-center gap-2.5">
				<VendorIcon name={vendor} vendor={vendor} size="xs" />
				<span className={cn(NAME, 'shrink-0 font-semibold')}>{vendor}</span>
				<span className="text-muted-foreground min-w-0 truncate text-[12.5px]">
					<span aria-hidden="true">· </span>
					{names.join(', ')}
					{moreText && <span className="text-foreground-faint">{moreText}</span>}
				</span>
			</div>
			<span role="cell" className="text-foreground-faint hidden truncate text-xs sm:block">
				{formatVendorCount(total)}
			</span>
			<span role="cell" className="hidden sm:block" />
			<span role="cell" className="flex items-center">
				<Button
					variant="ghost"
					size="xs"
					aria-expanded={false}
					aria-label={`Show all ${formatVendorCount(total)} from ${vendor}: ${names.join(', ')}${moreText}`}
					onClick={(e) => {
						e.stopPropagation();
						onExpand();
					}}
					className="group-hover/row:text-foreground -ml-2 px-2"
					data-testid="catalog-vendor-toggle"
				>
					<ChevronRight size={14} aria-hidden="true" />
					Show all
				</Button>
			</span>
		</LedgerRow>
	);
}

/**
 * The not-yet-loaded letters between the head of the catalog and a rail
 * jump's range. Fills from the head as it scrolls into view (the ledger
 * observes it); the button does the same on demand.
 */
export function CatalogGapRow({
	from,
	to,
	loading,
	onLoad,
	ref,
}: {
	from: string;
	to: string;
	loading: boolean;
	onLoad: () => void;
	ref?: Ref<HTMLDivElement>;
}) {
	const span = from === to ? from : `${from}–${to}`;
	return (
		<LedgerRow ref={ref} interactive={false} className="h-11" data-testid="catalog-gap">
			<div
				role="cell"
				className="text-foreground-faint col-span-full flex min-w-0 items-center gap-3 text-xs"
			>
				<span
					className="border-hairline-field h-px flex-1 border-t border-dashed"
					aria-hidden="true"
				/>
				<span>{span} not loaded yet</span>
				<Button
					variant="ghost"
					size="xs"
					loading={loading}
					onClick={onLoad}
					className="-my-1 px-2"
					data-testid="catalog-gap-load"
				>
					<ChevronUp size={14} aria-hidden="true" />
					Load earlier
				</Button>
				<span
					className="border-hairline-field h-px flex-1 border-t border-dashed"
					aria-hidden="true"
				/>
			</div>
		</LedgerRow>
	);
}
