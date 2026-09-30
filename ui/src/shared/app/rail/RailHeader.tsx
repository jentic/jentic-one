/**
 * RailHeader — the Activity rail's controls, in two rows:
 *
 *   1. live-status dot + "Activity" (+ one "Reconnecting…" / "Paused" word),
 *      then pause/play, the ⋯ menu, and collapse (or close, in the drawer)
 *   2. the Filter control (who / what — see `RailFilter`) and "Failures only"
 *
 * The ⋯ menu holds the rarely-touched things: load older and export.
 * Notification preferences (toasts, sound) live in the top-bar bell.
 *
 * Stateless: the rail body owns the state, this just emits change events.
 */
import { useRef, useState, type ReactNode } from 'react';
import { ChevronRight, Download, History, MoreHorizontal, Pause, Play, X } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { MenuPanel, menuItemClass, useDismissable } from '@/shared/ui';
import type { StreamStatus } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';
import { LiveDot, type LiveDotTone } from '@/shared/app/rail/LiveDot';

export type RailHeaderProps = {
	status: StreamStatus;
	paused: boolean;
	onTogglePause: () => void;
	/** The Filter control (`RailFilter`), rendered beside Failures only. */
	filter: ReactNode;
	failuresOnly: boolean;
	onToggleFailuresOnly: () => void;
	/** Unacknowledged failures under the current lens. */
	failureCount: number;
	onLoadOlder: () => void;
	canLoadOlder?: boolean;
	loadingOlder?: boolean;
	onExportTraceBundle: () => void;
	/** Docked rail: collapse to the strip. Drawer: close the sheet. */
	onCollapse: () => void;
	variant?: 'rail' | 'drawer';
};

function statusDot(status: StreamStatus, paused: boolean): { label: string; tone: LiveDotTone } {
	if (paused) return { label: 'Feed paused', tone: 'warning' };
	if (status === 'live') return { label: 'Stream live', tone: 'live' };
	if (status === 'error') return { label: 'Stream offline', tone: 'warning' };
	return { label: 'Connecting to stream', tone: 'connecting' };
}

export function RailHeader({
	status,
	paused,
	onTogglePause,
	filter,
	failuresOnly,
	onToggleFailuresOnly,
	failureCount,
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
		<div className="border-border border-b">
			<div className="flex items-center gap-2 py-2 pr-2 pl-3">
				<LiveDot tone={dot.tone} label={dot.label} />
				<span className="text-foreground text-sm font-semibold">Activity</span>
				{status === 'error' ? (
					<span
						className="text-warning truncate text-[11px]"
						title="The live stream dropped. Retrying — showing what's already loaded."
					>
						Reconnecting…
					</span>
				) : paused ? (
					<span className="text-muted-foreground truncate text-[11px]">Paused</span>
				) : null}
				<div className="ml-auto flex shrink-0 items-center">
					<Button
						variant="ghost"
						size="icon"
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
							size="icon"
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
					<Button
						variant="ghost"
						size="icon"
						onClick={onCollapse}
						aria-label={variant === 'drawer' ? 'Close activity' : 'Collapse activity'}
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

			<div className="flex items-center gap-2 px-3 pb-2.5">
				{filter}
				<button
					type="button"
					onClick={onToggleFailuresOnly}
					aria-pressed={failuresOnly}
					className={cn(
						'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2 text-xs font-medium transition-colors',
						failuresOnly
							? 'border-danger/50 bg-danger/10 text-danger'
							: 'border-border text-muted-foreground hover:text-foreground',
					)}
				>
					Failures only
					{failureCount > 0 && (
						<span
							className="bg-danger/20 text-danger rounded-full px-1.5 text-[10px] leading-4 font-semibold tabular-nums"
							aria-label={`${failureCount} unacknowledged`}
						>
							{failureCount > 99 ? '99+' : failureCount}
						</span>
					)}
				</button>
			</div>
		</div>
	);
}
