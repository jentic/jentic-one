import { useEffect, useMemo, useRef, useState } from 'react';
import { motion, type Variants } from 'framer-motion';
import { ChevronRight, Loader2, PencilLine, Search, SearchX, Sparkles } from 'lucide-react';
import { AgentBadge, Badge, EmptyState, ErrorAlert, Input, LoadingState } from '@/shared/ui';
import { useDebouncedValue } from '@/shared/hooks';
import {
	useApis,
	useCatalog,
	type ApiResponse,
	type CatalogEntryResponse,
	type SelectedApi,
} from '@/modules/credentials/api';

/**
 * Step 1 of the guided add-credential flow — a debounced search over the
 * combined "workspace + public catalog" API surface.
 *
 * Self-contained: owns its own input state, debounce, autofocus, and data
 * fetching. Parents only handle `onSelect` and the "Enter manually" escape.
 *
 * Two-endpoint merge (unlike jentic-mini's single `/apis?q=`):
 *  - `GET /apis` is unfiltered (no `q`); we client-filter by the same query
 *    so users see their workspace matches first.
 *  - `GET /catalog?q=` is the search-driven side; only fires once the user
 *    types something (the catalog manifest is 10k+ entries).
 */
export interface ApiPickerProps {
	onSelect: (api: SelectedApi) => void;
	/** Escape hatch — drop into the legacy free-text API reference form. */
	onManualEntry: () => void;
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

function localToSelected(row: ApiResponse): SelectedApi {
	const ref = row.api;
	const label = row.display_name ?? `${ref.vendor}/${ref.name}`;
	return {
		source: 'local',
		vendor: ref.vendor,
		name: ref.name,
		version: ref.version,
		securitySchemeTypes: row.security_schemes ?? [],
		label,
	};
}

function catalogToSelected(entry: CatalogEntryResponse): SelectedApi {
	// Catalog `api_id` is a flat slug (e.g. "stripe.com"). We split path-like
	// entries into vendor/name; otherwise we fall back to using the slug as
	// both. Version isn't on the catalog entry, so we default to "1.0.0".
	const slug = entry.api_id;
	const parts = (entry.path ?? slug).split('/').filter(Boolean);
	const vendor = entry.vendor ?? parts[0] ?? slug;
	const name = parts[1] ?? 'main';
	const version = parts[2] ?? '1.0.0';
	return {
		source: 'catalog',
		vendor,
		name,
		version,
		apiId: slug,
		specUrl: entry.spec_url ?? undefined,
		registered: entry.registered,
		label: slug,
	};
}

export function ApiPicker({ onSelect, onManualEntry }: ApiPickerProps) {
	const [query, setQuery] = useState('');
	const debouncedQuery = useDebouncedValue(query, 250);
	const inputRef = useRef<HTMLInputElement>(null);

	useEffect(() => {
		inputRef.current?.focus();
	}, []);

	const apisQuery = useApis({});
	const catalogQuery = useCatalog(debouncedQuery);

	const localRows = useMemo(() => {
		const q = debouncedQuery.trim().toLowerCase();
		const rows = apisQuery.data?.data ?? [];
		if (!q) return rows;
		return rows.filter((r) => {
			const hay = [r.display_name, r.description, r.api?.vendor, r.api?.name, r.api?.host]
				.filter(Boolean)
				.join(' ')
				.toLowerCase();
			return hay.includes(q);
		});
	}, [apisQuery.data, debouncedQuery]);

	const catalogRows = useMemo(() => {
		if (!debouncedQuery.trim()) return [];
		// Hide catalog entries that already match a workspace API to avoid
		// duplicate-looking rows. We must compare on the SAME derived key shape:
		// `catalogToSelected` splits the catalog `path`/`api_id` slug into
		// vendor/name, so we key both sides on that resolved `vendor/name` (a raw
		// `e.path` like "stripe.com/main/1.0.0" would never match "stripe/main").
		const localKeys = new Set(
			localRows.map((r) => `${r.api.vendor}/${r.api.name}`.toLowerCase()),
		);
		return (catalogQuery.data?.data ?? []).filter((e) => {
			const sel = catalogToSelected(e);
			return !localKeys.has(`${sel.vendor}/${sel.name}`.toLowerCase());
		});
	}, [catalogQuery.data, debouncedQuery, localRows]);

	const isSearching = catalogQuery.isFetching && !!debouncedQuery.trim();
	const error = (apisQuery.error ?? catalogQuery.error) as Error | null;
	const showLoading = apisQuery.isLoading && !apisQuery.data;
	const noResults =
		!isSearching &&
		!showLoading &&
		debouncedQuery.trim().length > 0 &&
		localRows.length === 0 &&
		catalogRows.length === 0;
	const isInitialEmpty = !showLoading && !debouncedQuery && localRows.length === 0 && !error;

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
				<button
					type="button"
					onClick={onManualEntry}
					className="text-muted-foreground hover:text-foreground hover:bg-muted/60 inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-colors"
				>
					<PencilLine className="h-3.5 w-3.5" />
					Enter manually
				</button>
			</div>

			{error && <ErrorAlert message={error} />}

			{showLoading && <LoadingState message="Loading workspace APIs…" />}

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
								<LocalApiRow row={row} onSelect={onSelect} />
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
						<p className="text-muted-foreground/80 mb-2 text-xs">
							Picking a catalog API imports it into your workspace as part of saving
							this credential.
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
								<CatalogRow entry={entry} onSelect={onSelect} />
							</motion.li>
						))}
					</motion.ul>
				</section>
			)}

			{noResults && (
				<EmptyState
					icon={<SearchX className="h-8 w-8" />}
					title="No APIs found"
					description={`Nothing matched "${debouncedQuery}". Try a different search, or enter an API manually.`}
				/>
			)}

			{isInitialEmpty && (
				<div className="border-border bg-muted/30 flex flex-col items-center gap-2 rounded-xl border border-dashed py-10 text-center">
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
			className="text-muted-foreground mb-1.5 px-1 font-mono text-[10px] tracking-widest uppercase"
		>
			{children}
		</p>
	);
}

function LocalApiRow({
	row,
	onSelect,
}: {
	row: ApiResponse;
	onSelect: (api: SelectedApi) => void;
}) {
	const selected = localToSelected(row);
	const badgeKey = `${selected.vendor}/${selected.name}`;
	return (
		<button
			type="button"
			onClick={(): void => onSelect(selected)}
			data-testid="picker-row"
			data-source="local"
			className="group hover:border-primary/50 bg-background hover:bg-muted/40 border-border flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-sm"
		>
			<AgentBadge id={badgeKey} name={selected.label} kind="API" size="sm" />
			<div className="min-w-0 flex-1">
				<span className="text-foreground block truncate text-sm font-medium">
					{selected.label}
				</span>
				<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
					{selected.vendor}/{selected.name}@{selected.version}
				</p>
			</div>
			{selected.securitySchemeTypes && selected.securitySchemeTypes.length > 0 && (
				<div className="flex shrink-0 gap-1">
					{selected.securitySchemeTypes.slice(0, 2).map((t) => (
						<Badge key={t} variant="default" className="text-[10px]">
							{prettySchemeType(t)}
						</Badge>
					))}
				</div>
			)}
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</button>
	);
}

function CatalogRow({
	entry,
	onSelect,
}: {
	entry: CatalogEntryResponse;
	onSelect: (api: SelectedApi) => void;
}) {
	const selected = catalogToSelected(entry);
	const badgeKey = `catalog:${selected.apiId ?? selected.label}`;
	return (
		<button
			type="button"
			onClick={(): void => onSelect(selected)}
			data-testid="picker-row"
			data-source="catalog"
			className="group hover:border-primary/50 bg-background hover:bg-muted/40 border-border flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-sm"
		>
			<AgentBadge id={badgeKey} name={selected.label} kind="API" size="sm" />
			<div className="min-w-0 flex-1">
				<span className="text-foreground block truncate text-sm font-medium">
					{selected.label}
				</span>
				<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
					{selected.vendor}
				</p>
			</div>
			{entry.registered && (
				<Badge variant="success" className="shrink-0 text-[10px]">
					Imported
				</Badge>
			)}
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
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
