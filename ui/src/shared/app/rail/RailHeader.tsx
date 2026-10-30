/**
 * RailHeader — the rail's controls:
 *
 *   • `RailHeader`, the top row: the Inbox | Activity tabs (the Activity tab
 *     carries the live-status dot), then — on Activity — pause/play and the ⋯
 *     menu, and always collapse (or close, in the drawer)
 *   • `RailActivityFilters`, the Activity tab's second row: one "Reconnecting…"
 *     / "Paused" / "No access" word when the stream is not live, the Filter
 *     control (who / what — see `RailFilter`) and "Failures only"
 *
 * The ⋯ menu holds the rarely-touched things: load older and export.
 * Notification preferences (toasts, sound) live in the top-bar bell.
 *
 * Stateless: the rail body owns the state, these just emit change events.
 */
import { useRef, useState, type ReactNode } from 'react';
import { ChevronRight, Download, History, MoreHorizontal, Pause, Play, X } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { MenuPanel, menuItemClass, useDismissable } from '@/shared/ui';
import type { StreamStatus } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';
import { LiveDot, type LiveDotTone } from '@/shared/app/rail/LiveDot';
import { RailTabs } from '@/shared/app/rail/RailTabs';
import type { RailTab } from '@/shared/app/rail/railPreferences';

export type RailHeaderProps = {
	/** Prefix for the tab and panel ids, unique per rail body. */
	baseId: string;
	tab: RailTab;
	onTabChange: (tab: RailTab) => void;
	/** Held calls waiting on the viewer, for the Inbox tab's count. */
	inboxCount: number;
	inboxAtLeast?: boolean;
	status: StreamStatus;
	paused: boolean;
	onTogglePause: () => void;
	onLoadOlder: () => void;
	canLoadOlder?: boolean;
	loadingOlder?: boolean;
	onExportTraceBundle: () => void;
	/** Docked rail: collapse to the strip. Drawer: close the sheet. */
	onCollapse: () => void;
	variant?: 'rail' | 'drawer';
};

function statusDot(status: StreamStatus, paused: boolean): { label: string; tone: LiveDotTone } {
	if (status === 'forbidden') return { label: 'No access to activity', tone: 'idle' };
	if (paused) return { label: 'Feed paused', tone: 'warning' };
	if (status === 'live') return { label: 'Stream live', tone: 'live' };
	if (status === 'error') return { label: 'Stream offline', tone: 'warning' };
	return { label: 'Connecting to stream', tone: 'connecting' };
}

export function RailHeader({
	baseId,
	tab,
	onTabChange,
	inboxCount,
	inboxAtLeast,
	status,
	paused,
	onTogglePause,
	onLoadOlder,
	canLoadOlder = true,
	loadingOlder,
	onExportTraceBundle,
	onCollapse,
	variant = 'rail',
}: RailHeaderProps) {
	const [menuOpen, setMenuOpen] = useState(false);
	const menuRef = useDismissable<HTMLDivElement>(menuOpen, () => setMenuOpen(false));
	const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
	const dot = statusDot(status, paused);

	function closeMenu() {
		setMenuOpen(false);
		menuTriggerRef.current?.focus();
	}

	return (
		<div className="flex items-center gap-1 px-2 py-2">
			<RailTabs
				baseId={baseId}
				value={tab}
				onChange={onTabChange}
				inboxCount={inboxCount}
				inboxAtLeast={inboxAtLeast}
				activityDot={<LiveDot tone={dot.tone} label={dot.label} />}
			/>
			<div className="ml-auto flex shrink-0 items-center">
				{tab === 'activity' && (
					<>
						<Button
							variant="ghost"
							size="icon-xs"
							onClick={onTogglePause}
							aria-label={paused ? 'Resume live feed' : 'Pause live feed'}
							title={paused ? 'Resume' : 'Pause'}
						>
							{paused ? (
								<Play className="text-muted-foreground h-3.5 w-3.5" />
							) : (
								<Pause className="text-muted-foreground h-3.5 w-3.5" />
							)}
						</Button>
						<div
							ref={menuRef}
							className="relative"
							// Escape closes the menu only, not the drawer sheet around it.
							onKeyDown={(e) => {
								if (e.key !== 'Escape' || !menuOpen) return;
								e.stopPropagation();
								closeMenu();
							}}
						>
							<Button
								ref={menuTriggerRef}
								variant="ghost"
								size="icon-xs"
								onClick={() => setMenuOpen((o) => !o)}
								aria-haspopup="menu"
								aria-expanded={menuOpen}
								aria-label="Activity options"
								title="More"
							>
								<MoreHorizontal className="text-muted-foreground h-4 w-4" />
							</Button>
							{menuOpen && (
								<MenuPanel align="right" className="w-60">
									<button
										type="button"
										role="menuitem"
										onClick={() => {
											closeMenu();
											onLoadOlder();
										}}
										disabled={loadingOlder || !canLoadOlder}
										className={cn(menuItemClass(), 'disabled:opacity-50')}
									>
										<History className="h-3.5 w-3.5" />
										{loadingOlder
											? 'Loading older…'
											: canLoadOlder
												? 'Load older events'
												: 'No older events'}
									</button>
									<button
										type="button"
										role="menuitem"
										onClick={() => {
											closeMenu();
											onExportTraceBundle();
										}}
										className={menuItemClass()}
									>
										<Download className="h-3.5 w-3.5" />
										Export last 5 minutes
									</button>
								</MenuPanel>
							)}
						</div>
					</>
				)}
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={onCollapse}
					aria-label={`${variant === 'drawer' ? 'Close' : 'Collapse'} ${tab === 'inbox' ? 'inbox' : 'activity'}`}
					title={variant === 'drawer' ? 'Close' : 'Collapse'}
				>
					{variant === 'drawer' ? (
						<X className="text-muted-foreground h-4 w-4" />
					) : (
						<ChevronRight className="text-muted-foreground h-4 w-4" />
					)}
				</Button>
			</div>
		</div>
	);
}

export type RailActivityFiltersProps = {
	status: StreamStatus;
	paused: boolean;
	/** The Filter control (`RailFilter`), rendered beside Failures only. */
	filter: ReactNode;
	failuresOnly: boolean;
	onToggleFailuresOnly: () => void;
	/** Recent failures (in the loaded feed) under the current lens. */
	failureCount: number;
};

/** The Activity tab's filter row, with the stream's state word when it is not live. */
export function RailActivityFilters({
	status,
	paused,
	filter,
	failuresOnly,
	onToggleFailuresOnly,
	failureCount,
}: RailActivityFiltersProps) {
	return (
		<div className="border-hairline flex items-center gap-2 border-b px-3 pb-2.5">
			{status === 'forbidden' ? (
				<span className="text-muted-foreground shrink-0 text-[11px]">No access</span>
			) : status === 'error' ? (
				<span
					className="text-warning shrink-0 text-[11px]"
					title="The live stream dropped. Retrying — showing what's already loaded."
				>
					Reconnecting…
				</span>
			) : paused ? (
				<span className="text-muted-foreground shrink-0 text-[11px]">Paused</span>
			) : null}
			{filter}
			<button
				type="button"
				onClick={onToggleFailuresOnly}
				aria-pressed={failuresOnly}
				className={cn(
					'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-xs font-medium transition-colors',
					failuresOnly
						? 'border-danger/50 bg-danger/10 text-danger'
						: 'border-hairline-field bg-surface-field text-muted-foreground hover:bg-surface-tonal hover:text-foreground',
				)}
			>
				Failures only
				{failureCount > 0 && (
					<span
						className="bg-danger/20 text-danger rounded-full px-1.5 text-[10px] leading-4 font-semibold tabular-nums"
						aria-label={`${failureCount} recent`}
					>
						{failureCount > 99 ? '99+' : failureCount}
					</span>
				)}
			</button>
		</div>
	);
}
