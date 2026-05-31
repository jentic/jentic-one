import type { ReactNode } from 'react';

/**
 * A labeled region inside a sectioned Discovery layout (`/workspace`).
 *
 * Used twice on the Workspace page:
 *
 *   <DiscoverySection
 *     id="your-workspace"
 *     icon={<Bookmark />}
 *     title="Your workspace"
 *     count={12}
 *   >
 *     {workspaceGrid}
 *   </DiscoverySection>
 *
 *   <DiscoverySection
 *     id="from-the-catalog"
 *     icon={<Compass />}
 *     title="From the catalog"
 *     count={4213}
 *     rightSlot={<AppLink href="/discover">Browse all in Discover →</AppLink>}
 *   >
 *     {catalogGrid}
 *   </DiscoverySection>
 *
 * The section header is the *primary* identity anchor for the user — it
 * tells them which region they're scanning at any scroll position. We
 * deliberately do NOT collapse it under a button by default: research
 * (NN/g, Refactoring UI, VSCode #68527) is clear that hidden sections
 * recreate the very confusion sectioning is meant to fix.
 */
export interface DiscoverySectionProps {
	/** Stable id for the section — used as a `data-testid` and anchor target. */
	id: string;
	/** Lucide icon (or any ReactNode) rendered before the title. */
	icon?: ReactNode;
	/** Human-readable section title. */
	title: string;
	/** Optional count rendered next to the title (e.g. `4213` → `· 4,213`). */
	count?: number;
	/** Optional right-aligned slot for actions/links (e.g. "Browse all"). */
	rightSlot?: ReactNode;
	children: ReactNode;
	className?: string;
}

export function DiscoverySection({
	id,
	icon,
	title,
	count,
	rightSlot,
	children,
	className,
}: DiscoverySectionProps) {
	return (
		<section data-testid={`discovery-section-${id}`} className={`space-y-3 ${className ?? ''}`}>
			<header
				className="flex items-baseline justify-between gap-3"
				data-testid={`discovery-section-header-${id}`}
			>
				<div className="flex items-baseline gap-2">
					{icon ? (
						<span aria-hidden="true" className="text-muted-foreground/80 self-center">
							{icon}
						</span>
					) : null}
					<h2 className="text-foreground text-base font-semibold tracking-tight">
						{title}
					</h2>
					{typeof count === 'number' && (
						<span
							className="text-muted-foreground/80 font-mono text-xs"
							data-testid={`discovery-section-count-${id}`}
						>
							· {count.toLocaleString()}
						</span>
					)}
				</div>
				{rightSlot ? <div className="shrink-0 text-sm">{rightSlot}</div> : null}
			</header>
			<div>{children}</div>
		</section>
	);
}
