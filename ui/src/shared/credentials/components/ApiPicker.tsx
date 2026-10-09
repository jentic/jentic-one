import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { motion, type Variants } from 'framer-motion';
import {
	Check,
	ChevronRight,
	Loader2,
	PencilLine,
	Search,
	SearchX,
	Sparkles,
	Zap,
} from 'lucide-react';
import { Badge, EmptyState, ErrorAlert, Input, LoadingState, Tag, VendorIcon } from '@/shared/ui';
import { useDebouncedValue } from '@/shared/hooks';
import { apiRefDisplayName } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import { apiRefKey } from '@/shared/credentials/lib/apiIdentity';
import {
	apiRowToSelected,
	catalogToSelected,
	useApis,
	useCatalog,
	useVendors,
	type ApiResponse,
	type CatalogEntryResponse,
	type SelectedApi,
	type VendorSummary,
} from '@/shared/credentials/api';

/**
 * A debounced search over the combined "workspace + public catalog" API
 * surface. Used single-select by the guided add-credential flow (a pick is the
 * commit) and multi-select by the agents surface's Add-APIs tray.
 *
 * Self-contained: owns its own input state, debounce, autofocus, and data
 * fetching. Parents only handle `onSelect` and the "Enter manually" escape.
 *
 * Two-endpoint merge:
 *  - `GET /apis` is unfiltered (no `q`); we client-filter by the same query
 *    so users see their workspace matches first.
 *  - `GET /catalog?q=` is the search-driven side; only fires once the user
 *    types something (the catalog manifest is 10k+ entries).
 */
export interface ApiPickerProps {
	/** A row was activated: the commit in single-select, a toggle in multi-select. */
	onSelect: (api: SelectedApi) => void;
	/**
	 * User picked a verified vendor (agent-driven SSO / device-flow path) — the
	 * caller should switch to the vendor connect flow instead of building the
	 * credential form. The picker sits above this by design: verified vendors
	 * are the "one-click sign-in" path; API + manual entry are the fallback.
	 * Omit (e.g. in multi-select mode) to hide the verified section.
	 */
	onVendorSelect?: (vendor: VendorSummary) => void;
	/** Escape hatch — drop into the legacy free-text API reference form.
	 *  Omit to hide the affordance (the tray offers spec upload instead). */
	onManualEntry?: () => void;
	/** Multi-select mode: the `apiRefKey`s currently picked. Passing this — even
	 * empty — switches rows from drill-in buttons to checkboxes. */
	selectedKeys?: ReadonlySet<string>;
	/** Rows that cannot be picked, by `apiRefKey`. */
	disabledKeys?: ReadonlySet<string>;
	/** Short badge explaining why a `disabledKeys` row is out (e.g. "Already added");
	 * a function labels each row by its `apiRefKey`. */
	disabledLabel?: string | ((key: string) => string | undefined);
	/** A quiet note on a row that stays pickable (e.g. "Added via GitHub — personal"),
	 * by `apiRefKey`. */
	rowHint?: (key: string) => string | undefined;
	/** The search box, for a host that must move focus there itself (e.g. a sheet
	 * re-opened without remounting the picker). */
	searchInputRef?: RefObject<HTMLInputElement | null>;
	/** Rendered in the no-results state, the one moment the operator has proved the
	 * API they want isn't here. The tray passes its spec upload. */
	emptyAction?: ReactNode;
}

/** Stagger the result rows in so a fresh search feels responsive, not janky. */
const LIST_VARIANTS: Variants = {
	hidden: {},
	show: { transition: { staggerChildren: 0.035 } },
};

const ROW_VARIANTS: Variants = {
	hidden: { opacity: 0, y: 6 },
	show: { opacity: 1, y: 0, transition: { duration: 0.18, ease: 'easeOut' } },
};

export function ApiPicker({
	onSelect,
	onVendorSelect,
	onManualEntry,
	selectedKeys,
	disabledKeys,
	disabledLabel,
	rowHint,
	emptyAction,
	searchInputRef,
}: ApiPickerProps) {
	const [query, setQuery] = useState('');
	const debouncedQuery = useDebouncedValue(query, 250);
	const ownInputRef = useRef<HTMLInputElement>(null);
	const inputRef = searchInputRef ?? ownInputRef;

	// Focus on mount. The ref is stable (a host's ref object, or our own), so this
	// still runs once.
	useEffect(() => {
		inputRef.current?.focus();
	}, [inputRef]);

	const apisQuery = useApis({});
	const catalogQuery = useCatalog(debouncedQuery);
	const vendorsQuery = useVendors();

	// Verified vendors — filter by query. The verified section is only meaningful
	// if the parent wired up `onVendorSelect`; otherwise there's nowhere to route.
	const filteredVendors = useMemo(() => {
		if (!onVendorSelect) return [];
		const rows = vendorsQuery.data?.data ?? [];
		const q = debouncedQuery.trim().toLowerCase();
		if (!q) return rows;
		return rows.filter(
			(v) =>
				v.display_name.toLowerCase().includes(q) ||
				v.name.toLowerCase().includes(q) ||
				v.vendor.toLowerCase().includes(q) ||
				v.key.toLowerCase().includes(q),
		);
	}, [onVendorSelect, vendorsQuery.data, debouncedQuery]);

	const localRows = useMemo(() => {
		const q = debouncedQuery.trim().toLowerCase();
		const rows = apisQuery.data?.data ?? [];
		if (!q) return rows;
		return rows.filter((r) => {
			// Include the friendly display name (what the row actually shows) in
			// the searchable text, so typing what's on screen — e.g. `Posthog.Com`
			// for a `posthog-com` vendor with no explicit `display_name` — returns
			// the row instead of only matching the raw machine identity.
			const friendly = apiRefDisplayName({
				displayName: r.display_name,
				catalogApiId: r.catalog_api_id,
				vendor: r.api?.vendor,
				name: r.api?.name,
			});
			const hay = [
				r.display_name,
				friendly,
				r.catalog_api_id,
				r.description,
				r.api?.vendor,
				r.api?.name,
				r.api?.host,
			]
				.filter(Boolean)
				.join(' ')
				.toLowerCase();
			return hay.includes(q);
		});
	}, [apisQuery.data, debouncedQuery]);

	const catalogRows = useMemo(() => {
		if (!debouncedQuery.trim()) return [];
		// Hide catalog entries already imported into the workspace, so one API
		// never lists twice. Both sides key in slug form: a workspace row stores
		// `abstractapi-com`, the catalog entry says `abstractapi.com`.
		const localKeys = new Set(localRows.map((r) => apiRefKey(r.api)));
		return (catalogQuery.data?.data ?? []).filter(
			(e) => !localKeys.has(apiRefKey(catalogToSelected(e))),
		);
	}, [catalogQuery.data, debouncedQuery, localRows]);

	const isSearching = catalogQuery.isFetching && !!debouncedQuery.trim();
	const error = (apisQuery.error ?? catalogQuery.error) as Error | null;
	const showLoading = apisQuery.isLoading && !apisQuery.data;
	const noResults =
		!isSearching &&
		!showLoading &&
		debouncedQuery.trim().length > 0 &&
		filteredVendors.length === 0 &&
		localRows.length === 0 &&
		catalogRows.length === 0;
	const isInitialEmpty =
		!showLoading &&
		!debouncedQuery &&
		filteredVendors.length === 0 &&
		localRows.length === 0 &&
		!error;

	// Presence, not emptiness, switches the rows into checkbox mode.
	const selection: RowSelection | undefined = selectedKeys
		? { selectedKeys, disabledKeys, disabledLabel, rowHint }
		: undefined;

	return (
		<div className="space-y-4">
			<div className="flex items-center gap-2">
				<div className="relative flex-1">
					<Input
						ref={inputRef}
						type="text"
						value={query}
						onChange={(e): void => setQuery(e.target.value)}
						placeholder="Search APIs (GitHub, Gmail, Stripe…)"
						aria-label="Search APIs"
						startIcon={<Search className="h-4 w-4" />}
					/>
					{isSearching && (
						<Loader2 className="text-muted-foreground absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 animate-spin" />
					)}
				</div>
				{onManualEntry && (
					<button
						type="button"
						onClick={onManualEntry}
						className="text-muted-foreground hover:text-foreground hover:bg-tint-2 focus-visible:ring-ring inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors focus-visible:ring-2 focus-visible:outline-none"
					>
						<PencilLine className="h-3.5 w-3.5" />
						Enter manually
					</button>
				)}
			</div>

			{error && <ErrorAlert message={error} />}

			{showLoading && <LoadingState message="Loading workspace APIs…" />}

			{filteredVendors.length > 0 && onVendorSelect && (
				<section aria-labelledby="picker-vendors-heading">
					<SectionHeading id="picker-vendors-heading">
						<span className="inline-flex items-center gap-1.5">
							<Zap className="text-primary h-3 w-3" />
							One-click sign-in
						</span>
					</SectionHeading>
					<motion.ul
						className="grid gap-2 sm:grid-cols-2"
						variants={LIST_VARIANTS}
						initial="hidden"
						animate="show"
					>
						{filteredVendors.map((vendor) => (
							<motion.li key={vendor.entry_id} variants={ROW_VARIANTS}>
								<VendorTile vendor={vendor} onSelect={onVendorSelect} />
							</motion.li>
						))}
					</motion.ul>
				</section>
			)}

			{localRows.length > 0 && (
				<section aria-labelledby="picker-local-heading">
					<SectionHeading id="picker-local-heading">In your workspace</SectionHeading>
					<motion.ul
						className="space-y-1.5"
						variants={LIST_VARIANTS}
						initial="hidden"
						animate="show"
					>
						{localRows.slice(0, 12).map((row) => (
							<motion.li
								key={`${row.api.vendor}/${row.api.name}/${row.api.version}`}
								variants={ROW_VARIANTS}
							>
								<LocalApiRow row={row} onSelect={onSelect} selection={selection} />
							</motion.li>
						))}
					</motion.ul>
				</section>
			)}

			{catalogRows.length > 0 && (
				<section aria-labelledby="picker-catalog-heading">
					<SectionHeading id="picker-catalog-heading">
						From the Jentic public catalog
					</SectionHeading>
					{localRows.length === 0 && (
						<p className="text-muted-foreground mb-2 text-xs">
							{/* Multi-select hosts save no credential here, and they
							    tally the import count themselves. */}
							{selection
								? 'Picking a catalog API imports it into your workspace.'
								: 'Picking a catalog API imports it into your workspace as part of saving this credential.'}
						</p>
					)}
					<motion.ul
						className="space-y-1.5"
						variants={LIST_VARIANTS}
						initial="hidden"
						animate="show"
					>
						{catalogRows.slice(0, 20).map((entry) => (
							<motion.li key={entry.api_id} variants={ROW_VARIANTS}>
								<CatalogRow
									entry={entry}
									onSelect={onSelect}
									selection={selection}
								/>
							</motion.li>
						))}
					</motion.ul>
				</section>
			)}

			{noResults && (
				<EmptyState
					icon={<SearchX className="h-8 w-8" />}
					title="No APIs found"
					// Only offer what this host actually has: a picker without
					// manual entry and without an upload can only suggest a
					// different search, and promising either would be a lie.
					description={`Nothing matched "${debouncedQuery}". Try a different search${
						emptyAction
							? ', or add it from its OpenAPI spec.'
							: onManualEntry
								? ', or enter an API manually.'
								: '.'
					}`}
					action={emptyAction}
				/>
			)}

			{isInitialEmpty && (
				<div className="bg-field border-border/60 flex flex-col items-center gap-2 rounded-lg border border-dashed py-10 text-center">
					<Sparkles className="text-muted-foreground h-6 w-6" />
					<p className="text-foreground text-sm font-medium">
						Search 10,000+ APIs from the public catalog
					</p>
					<p className="text-muted-foreground max-w-xs text-xs">
						Start typing the vendor, host, or service name. Picking an API auto-shapes
						the credential form from its OpenAPI spec.
					</p>
				</div>
			)}
		</div>
	);
}

function SectionHeading({ id, children }: { id: string; children: React.ReactNode }) {
	return (
		<p
			id={id}
			className="text-foreground-faint mb-1.5 px-1 text-[10.5px] font-bold tracking-[0.08em] uppercase"
		>
			{children}
		</p>
	);
}

/** Multi-select wiring, threaded from the props of the same name. */
interface RowSelection {
	selectedKeys: ReadonlySet<string>;
	disabledKeys?: ReadonlySet<string>;
	disabledLabel?: ApiPickerProps['disabledLabel'];
	rowHint?: ApiPickerProps['rowHint'];
}

/**
 * One result row. Single-select rows are drill-in buttons; multi-select rows
 * ARE the checkbox — `role="checkbox"` on the row itself, because the row owns
 * the click and nesting the `Checkbox` primitive would put a `<button>` inside
 * a `<button>`. Hence the presentational tick below rather than the primitive.
 */
function PickerRow({
	api,
	badgeKey,
	meta,
	trailing,
	source,
	onSelect,
	selection,
}: {
	api: SelectedApi;
	badgeKey: string;
	/** The machine-identity line under the title. */
	meta: ReactNode;
	/** Row-specific trailing content (auth badges, "Imported"). */
	trailing?: ReactNode;
	source: 'local' | 'catalog';
	onSelect: (api: SelectedApi) => void;
	selection?: RowSelection;
}) {
	const key = apiRefKey(api);
	const checked = selection ? selection.selectedKeys.has(key) : undefined;
	const blocked = selection?.disabledKeys?.has(key) ?? false;
	const blockedLabel =
		typeof selection?.disabledLabel === 'function'
			? selection.disabledLabel(key)
			: selection?.disabledLabel;
	const hint = blocked ? undefined : selection?.rowHint?.(key);
	return (
		<button
			type="button"
			role={selection ? 'checkbox' : undefined}
			aria-checked={selection ? checked : undefined}
			disabled={blocked}
			onClick={(): void => onSelect(api)}
			data-testid="picker-row"
			data-source={source}
			className={cn(
				'group bg-field focus-visible:ring-ring flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none',
				blocked ? 'cursor-not-allowed opacity-60' : 'hover:bg-surface-tonal',
				checked && 'bg-surface-selected shadow-[0_0_0_1.5px_hsl(var(--primary)/0.45)]',
			)}
		>
			{selection && <TickBox checked={!!checked} />}
			<VendorIcon name={api.label} vendor={badgeKey} size="sm" />
			<div className="min-w-0 flex-1">
				<span className="text-foreground-name block truncate text-sm font-semibold">
					{api.label}
				</span>
				<p className="text-foreground-sub mt-0.5 truncate font-mono text-xs">{meta}</p>
				{hint && (
					<p
						data-testid="picker-row-hint"
						className="text-muted-foreground mt-0.5 truncate text-xs"
					>
						{hint}
					</p>
				)}
			</div>
			{blocked && blockedLabel ? (
				<Badge variant="neutral" className="shrink-0">
					{blockedLabel}
				</Badge>
			) : (
				trailing
			)}
			{!selection && (
				<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
			)}
		</button>
	);
}

/** The tick, drawn to match the `Checkbox` primitive's box. */
function TickBox({ checked }: { checked: boolean }) {
	return (
		<span
			aria-hidden="true"
			className={cn(
				'flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors',
				checked ? 'border-primary bg-primary' : 'border-border border-2',
			)}
		>
			{checked && <Check className="text-primary-foreground h-3 w-3" />}
		</span>
	);
}

function LocalApiRow({
	row,
	onSelect,
	selection,
}: {
	row: ApiResponse;
	onSelect: (api: SelectedApi) => void;
	selection?: RowSelection;
}) {
	const api = apiRowToSelected(row);
	const schemes = api.securitySchemeTypes ?? [];
	return (
		<PickerRow
			api={api}
			badgeKey={`${api.vendor}/${api.name}`}
			meta={`${api.vendor}/${api.name}@${api.version}`}
			source="local"
			onSelect={onSelect}
			selection={selection}
			trailing={
				schemes.length > 0 && (
					<div className="flex shrink-0 gap-1">
						{schemes.slice(0, 2).map((t) => (
							<Tag key={t}>{prettySchemeType(t)}</Tag>
						))}
					</div>
				)
			}
		/>
	);
}

function CatalogRow({
	entry,
	onSelect,
	selection,
}: {
	entry: CatalogEntryResponse;
	onSelect: (api: SelectedApi) => void;
	selection?: RowSelection;
}) {
	const api = catalogToSelected(entry);
	return (
		<PickerRow
			api={api}
			badgeKey={`catalog:${api.apiId ?? api.label}`}
			// The full machine identity, not just the vendor: two entries whose
			// sub-segments humanise identically must stay distinguishable.
			meta={api.apiId}
			source="catalog"
			onSelect={onSelect}
			selection={selection}
			trailing={
				entry.registered && (
					<Badge variant="success" className="shrink-0">
						Imported
					</Badge>
				)
			}
		/>
	);
}

/** One one-click sign-in card: a platform vendor or an organization's shared OAuth app. */
export function VendorTile({
	vendor,
	onSelect,
}: {
	vendor: VendorSummary;
	onSelect: (vendor: VendorSummary) => void;
}) {
	return (
		<button
			type="button"
			onClick={(): void => onSelect(vendor)}
			data-testid="vendor-tile"
			className="group bg-field hover:bg-surface-tonal focus-visible:ring-ring relative flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
		>
			<VendorIcon name={vendor.display_name} vendor={vendor.vendor} size="md" />
			<div className="min-w-0 flex-1">
				<div className="flex min-w-0 items-center gap-2">
					<p className="text-foreground-name truncate text-sm font-semibold">
						{vendor.source === 'db'
							? vendor.name
							: `Sign in with ${vendor.display_name}`}
					</p>
					{/* Always badged: an admin app named like its API ("Gmail") would
					    otherwise read as the platform's own sign-in tile. */}
					{vendor.source === 'db' && (
						<Badge variant="default" className="shrink-0">
							Shared app
						</Badge>
					)}
				</div>
				<p className="text-muted-foreground mt-0.5 truncate text-xs">
					{vendor.source === 'db'
						? `${vendor.display_name} · set up by your organization`
						: 'Instant OAuth · no keys to copy'}
				</p>
			</div>
			<ChevronRight className="text-muted-foreground group-hover:text-primary h-4 w-4 shrink-0 transition-colors" />
		</button>
	);
}

/** OpenAPI scheme type names → short user-friendly labels for the row badges. */
function prettySchemeType(type: string): string {
	switch (type.toLowerCase()) {
		case 'apikey':
			return 'API Key';
		case 'http':
			return 'HTTP';
		case 'oauth2':
			return 'OAuth 2.0';
		case 'openidconnect':
			return 'OIDC';
		default:
			return type;
	}
}
