/**
 * WorkspaceFilterBar — sticky, gutter-bleeding filter row for the Workspace
 * page.
 *
 * Mirrors jentic-mini's `WorkspaceSearch` intent (an in-memory filter, *not* a
 * catalog search — hence the funnel icon rather than a magnifying glass) using
 * jentic-one's established sticky pattern (the same `sticky top-12` /
 * `-mx-page-gutter px-page-gutter` backdrop-blur bar the Discover toolbar uses,
 * with a sentinel + IntersectionObserver hairline shadow once it sticks).
 *
 * Catalog-wide search lives in Discover; this only narrows the rows already on
 * screen.
 */
import { useEffect, useRef } from 'react';
import { Filter } from 'lucide-react';
import { SearchInput } from '@/shared/ui';

export interface WorkspaceFilterBarProps {
	value: string;
	onChange: (next: string) => void;
	/** Optional summary line, e.g. "12 of 40". Shown only when filtering. */
	resultsLabel?: string;
}

export function WorkspaceFilterBar({ value, onChange, resultsLabel }: WorkspaceFilterBarProps) {
	const sentinelRef = useRef<HTMLDivElement | null>(null);
	const barRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		const sentinel = sentinelRef.current;
		const bar = barRef.current;
		if (!sentinel || !bar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				bar.dataset.scrolled = entry.isIntersecting ? 'false' : 'true';
			},
			{ threshold: 0 },
		);
		obs.observe(sentinel);
		return () => obs.disconnect();
	}, []);

	return (
		<div
			ref={barRef}
			data-scrolled="false"
			className="-mx-page-gutter px-page-gutter border-border/40 bg-background/85 sticky top-12 z-20 -mt-3 border-y py-3 backdrop-blur transition-shadow data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
			data-testid="workspace-filter-bar"
		>
			<div ref={sentinelRef} aria-hidden="true" className="absolute top-0 h-px w-full" />
			<div className="flex items-center gap-3">
				<div className="flex-1">
					<SearchInput
						value={value}
						onValueChange={onChange}
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter your workspace by name or description…"
						aria-label="Filter your APIs"
					/>
				</div>
				{value && resultsLabel ? (
					<p
						className="text-muted-foreground shrink-0 text-xs"
						data-testid="workspace-filter-results"
					>
						{resultsLabel}
					</p>
				) : null}
			</div>
		</div>
	);
}
