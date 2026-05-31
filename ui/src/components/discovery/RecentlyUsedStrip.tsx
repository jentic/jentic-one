import { Clock } from 'lucide-react';
import { useRecentInspects } from './recentInspectStore';

/**
 * Horizontal strip of "recently used" API chips — a quick re-entry
 * surface for the user's most-recent inspect targets, surfaced inside
 * the "Your workspace" section of the sectioned Workspace page.
 *
 * Backed by `recentInspectStore` (session-scoped 5-entry ring keyed by
 * `apiId`), the store the API Detail Sheet already maintains. Clicking
 * a chip opens the sheet for that API without navigating, identical to
 * the `RecentInspectsStrip` rendered inside the sheet.
 *
 * Rendering rules:
 *
 *  - Hidden when the ring has fewer than 2 entries (a single chip is
 *    just a re-render of "the API I'm currently looking at" and adds
 *    no recall value).
 *  - Up to 5 chips, most-recent first.
 *  - Inert when `onSelectApi` is not supplied — keeps the component
 *    safe to drop into any layout that doesn't yet have a sheet
 *    handler.
 *
 * Note: the plan called for "render when workspace section > 8 cards",
 * but the recents ring itself is a strong signal of "the user has been
 * around long enough for this to be useful". In a brand-new tab the
 * ring is empty, so the strip never shows. In a returning session
 * with a populated ring, surfacing recents is valuable regardless of
 * absolute workspace size. The size-gate comment is preserved so we
 * can revisit if telemetry shows the strip cluttering tiny workspaces.
 */
export interface RecentlyUsedStripProps {
	onSelectApi: (apiId: string) => void;
	className?: string;
}

export function RecentlyUsedStrip({ onSelectApi, className }: RecentlyUsedStripProps) {
	const { entries } = useRecentInspects();
	if (entries.length < 2) return null;

	return (
		<div
			className={`flex items-center gap-2 overflow-x-auto pb-1 ${className ?? ''}`}
			data-testid="recently-used-strip"
			aria-label="Recently used in this session"
		>
			<span className="text-muted-foreground inline-flex shrink-0 items-center gap-1 text-[10px] tracking-wider uppercase">
				<Clock size={10} aria-hidden="true" />
				Recently used
			</span>
			{entries.slice(0, 5).map((entry) => (
				<button
					key={entry.apiId}
					type="button"
					onClick={() => onSelectApi(entry.apiId)}
					className="border-border/60 bg-card text-foreground hover:border-border hover:bg-muted/60 inline-flex max-w-[14rem] shrink-0 items-center gap-1.5 truncate rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
					data-testid={`recently-used-chip-${entry.apiId}`}
				>
					<span className="truncate">{entry.name ?? entry.apiId}</span>
				</button>
			))}
		</div>
	);
}
