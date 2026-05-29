import { ArrowDown, Compass, FilterX, RefreshCw, Search } from 'lucide-react';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/EmptyState';

/**
 * Empty / zero-result state for the Discover surface.
 *
 * Five variants, each teaching the user the next IA-aligned action:
 *
 * 1. `cold-start` — Workspace is empty, no search/filter active.
 *    Encourages browsing the public directory or importing from URL.
 *    Used by single-mode pages (`/discover`).
 *
 *    @example
 *      <DiscoverEmptyState
 *        variant="cold-start"
 *        onSwitchToDirectory={() => setSource('directory')}
 *        importHref="/apis/new"
 *      />
 *
 * 2. `cold-start-sectioned` — Workspace section is empty on `/workspace`
 *    but the catalog section *is* populated below. Renders inline,
 *    short, and points the user *down* at the catalog where the
 *    answer already lives — the strongest disambiguation of the new
 *    sectioned IA. Use only inside a `<DiscoverySection>`; the
 *    parent must guarantee the catalog section is rendering below.
 *
 *    @example
 *      <DiscoverEmptyState variant="cold-start-sectioned" />
 *
 * 3. `catalog-degraded` — Catalog section can't load (network blip /
 *    catalog server down). Inline notice with a retry, no full-page
 *    takeover.
 *
 *    @example
 *      <DiscoverEmptyState variant="catalog-degraded" onRetry={refetch} />
 *
 * 4. `zero-search` — A query is active but produced zero hits. Surfaces
 *    vendor credential suggestions and inline switch shortcuts.
 *
 *    @example
 *      <DiscoverEmptyState
 *        variant="zero-search"
 *        query="strpie"
 *        vendorSuggestions={['stripe.com']}
 *        canSwitchType
 *        canSwitchSource
 *        onSwitchType={() => setType('all')}
 *        onSwitchSource={() => setSource('directory')}
 *      />
 *
 * 5. `filtered-empty` — Filters are narrowing the result set to nothing.
 *    Offers a single "Clear filters" escape hatch.
 *
 *    @example
 *      <DiscoverEmptyState
 *        variant="filtered-empty"
 *        onClearFilters={() => resetFilters()}
 *      />
 *
 * Wraps the generic `EmptyState` primitive so the visual shell matches
 * other empty surfaces. All variants render at the same outer size so
 * swapping between variants doesn't shift layout.
 */
export type DiscoverEmptyStateProps =
	| {
			variant: 'cold-start';
			onSwitchToDirectory: () => void;
			importHref?: string;
	  }
	| {
			variant: 'cold-start-sectioned';
	  }
	| {
			variant: 'catalog-degraded';
			onRetry?: () => void;
	  }
	| {
			variant: 'zero-search';
			query: string;
	  }
	| {
			variant: 'filtered-empty';
			onClearFilters: () => void;
			/**
			 * The kind of entity the active grid is rendering. Drives the
			 * empty-state copy so users see "No workflows match the current
			 * filters" on the workflows tab instead of the always-APIs
			 * default. Optional for back-compat — defaults to `'api'`.
			 */
			entityType?: 'api' | 'workflow';
	  };

export function DiscoverEmptyState(props: DiscoverEmptyStateProps): JSX.Element {
	if (props.variant === 'cold-start') {
		return <ColdStart {...props} />;
	}
	if (props.variant === 'cold-start-sectioned') {
		return <ColdStartSectioned />;
	}
	if (props.variant === 'catalog-degraded') {
		return <CatalogDegraded {...props} />;
	}
	if (props.variant === 'zero-search') {
		return <ZeroSearch {...props} />;
	}
	return <FilteredEmpty {...props} />;
}

function ColdStart({
	onSwitchToDirectory,
	importHref,
}: Extract<DiscoverEmptyStateProps, { variant: 'cold-start' }>) {
	const importLabel = 'Import from URL';
	const importButton = importHref ? (
		<AppLink
			href={importHref}
			data-testid="discover-empty-import-link"
			className="bg-muted border-border text-foreground hover:bg-muted/60 inline-flex items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium transition-all"
		>
			{importLabel}
		</AppLink>
	) : (
		// TODO: link to import flow once a canonical entry point exists.
		<Button
			variant="secondary"
			disabled
			title="Coming soon"
			data-testid="discover-empty-import-disabled"
		>
			{importLabel}
		</Button>
	);

	return (
		<EmptyState
			className="min-h-[280px]"
			icon={<Compass className="h-10 w-10" aria-hidden="true" />}
			title="Nothing in your workspace yet"
			description="Try the Jentic public catalog or import an API from URL"
			action={
				<div className="flex flex-wrap items-center justify-center gap-2">
					<Button
						variant="primary"
						onClick={onSwitchToDirectory}
						data-testid="discover-empty-browse-directory"
					>
						<Compass className="h-4 w-4" aria-hidden="true" />
						Browse the Jentic public catalog
					</Button>
					{importButton}
				</div>
			}
		/>
	);
}

function ZeroSearch({ query }: Extract<DiscoverEmptyStateProps, { variant: 'zero-search' }>) {
	return (
		<EmptyState
			className="min-h-[200px]"
			icon={<Search className="h-8 w-8" aria-hidden="true" />}
			title={`No APIs found for "${query}"`}
			description="Try a different name or check for typos."
		/>
	);
}

function FilteredEmpty({
	onClearFilters,
	entityType = 'api',
}: Extract<DiscoverEmptyStateProps, { variant: 'filtered-empty' }>) {
	const noun = entityType === 'workflow' ? 'workflows' : 'APIs';
	return (
		<EmptyState
			className="min-h-[280px]"
			icon={<FilterX className="h-10 w-10" aria-hidden="true" />}
			title={`No ${noun} match the current filters`}
			description={`Adjust the filters above or clear them to see all ${noun}.`}
			action={
				<Button
					variant="primary"
					onClick={onClearFilters}
					data-testid="discover-empty-clear-filters"
				>
					<FilterX className="h-4 w-4" aria-hidden="true" />
					Clear filters
				</Button>
			}
		/>
	);
}

/**
 * Inline empty state for the Workspace section when the user has no
 * imported APIs yet, but the catalog section *is* rendering below them.
 * The arrow-down icon is intentional — the answer is literally on the
 * page already, and this notice exists to point the user at it.
 */
function ColdStartSectioned() {
	return (
		<div
			className="border-border/60 bg-muted/20 text-muted-foreground flex items-start gap-3 rounded-xl border border-dashed p-4 text-sm"
			data-testid="discover-empty-cold-start-sectioned"
		>
			<ArrowDown
				className="text-muted-foreground/80 mt-0.5 h-4 w-4 shrink-0"
				aria-hidden="true"
			/>
			<div>
				<p className="text-foreground font-medium">Your workspace is empty.</p>
				<p className="mt-0.5">
					Pick an API from the catalog below — adding a credential imports it into your
					workspace.
				</p>
			</div>
		</div>
	);
}

/**
 * Inline empty state for a degraded catalog fetch. We don't take over
 * the page; the workspace section above stays interactive so the user
 * isn't held hostage by an upstream blip.
 */
function CatalogDegraded({
	onRetry,
}: Extract<DiscoverEmptyStateProps, { variant: 'catalog-degraded' }>) {
	return (
		<div
			className="border-border/60 bg-muted/20 text-muted-foreground flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed p-4 text-sm"
			data-testid="discover-empty-catalog-degraded"
		>
			<div className="flex items-start gap-3">
				<Compass
					className="text-muted-foreground/80 mt-0.5 h-4 w-4 shrink-0"
					aria-hidden="true"
				/>
				<div>
					<p className="text-foreground font-medium">Catalog unavailable.</p>
					<p className="mt-0.5">
						We couldn't reach the Jentic public catalog. Try refreshing — anything
						already imported into your workspace is unaffected.
					</p>
				</div>
			</div>
			{onRetry ? (
				<Button
					variant="ghost"
					size="sm"
					onClick={onRetry}
					data-testid="discover-empty-catalog-degraded-retry"
				>
					<RefreshCw className="h-4 w-4" aria-hidden="true" />
					Retry
				</Button>
			) : null}
		</div>
	);
}
