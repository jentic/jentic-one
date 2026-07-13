/**
 * CredentialsToolbar — sticky filter + type filter + refresh for the
 * credentials list.
 *
 * Mirrors the Discover module's toolbar treatment for cross-page parity: the
 * bar pins below the fixed `h-12` TopNavbar (`sticky top-12`), bleeds to the
 * page gutter edges (`-mx-page-gutter px-page-gutter`) so its backdrop-blur
 * covers the full width, and drops a hairline shadow only once it has actually
 * stuck (a zero-height sentinel + IntersectionObserver flips `data-scrolled`).
 *
 * Note: this is a *filter* over the credentials already loaded from the local
 * database, not a backend search — the page narrows its in-memory list by
 * name/vendor/provider client-side. Copy reflects that ("Filter…", not
 * "Search…").
 *
 * Layout (matching Discover): a full-width text filter takes the remaining
 * width, with the type filter + refresh pinned to the right.
 */
import { useEffect, useRef } from 'react';
import { Filter } from 'lucide-react';
import { RefreshButton, SearchInput, SegmentedToggle } from '@/shared/ui';
import {
	CREDENTIAL_TYPE_LABELS,
	CREDENTIAL_TYPE_ORDER,
	type CredentialType,
} from '@/modules/credentials/api';

/** `all` plus each credential type — the segmented filter's value space. */
export type CredentialTypeFilter = 'all' | CredentialType;

interface CredentialsToolbarProps {
	query: string;
	onQueryChange: (value: string) => void;
	filter: CredentialTypeFilter;
	onFilterChange: (value: CredentialTypeFilter) => void;
	onRefresh: () => void;
	/** Keeps the refresh glyph spinning while a refetch is in flight. */
	refreshing?: boolean;
}

const FILTER_OPTIONS: { value: CredentialTypeFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	...CREDENTIAL_TYPE_ORDER.map((type) => ({
		value: type as CredentialTypeFilter,
		label: CREDENTIAL_TYPE_LABELS[type],
	})),
];

export function CredentialsToolbar({
	query,
	onQueryChange,
	filter,
	onFilterChange,
	onRefresh,
	refreshing,
}: CredentialsToolbarProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);
	const toolbarRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		const sentinel = sentinelRef.current;
		const toolbar = toolbarRef.current;
		if (!sentinel || !toolbar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				toolbar.dataset.scrolled = entry.isIntersecting ? 'false' : 'true';
			},
			{ threshold: 0 },
		);
		obs.observe(sentinel);
		return () => obs.disconnect();
	}, []);

	return (
		<div
			ref={toolbarRef}
			data-scrolled="false"
			className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 border-b py-3 backdrop-blur transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			data-testid="credentials-toolbar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
				<div className="flex-1">
					<SearchInput
						value={query}
						onValueChange={onQueryChange}
						placeholder="Filter by name, vendor, or provider…"
						aria-label="Filter credentials"
						icon={<Filter className="h-3.5 w-3.5" />}
					/>
				</div>
				<div className="flex items-center gap-2">
					<SegmentedToggle
						layoutId="credentials-filter"
						options={FILTER_OPTIONS}
						value={filter}
						onChange={onFilterChange}
					/>
					<RefreshButton
						onRefresh={onRefresh}
						pending={refreshing}
						title="Refresh credentials"
						testId="credentials-refresh"
					/>
				</div>
			</div>
		</div>
	);
}
