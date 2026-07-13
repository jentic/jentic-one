import { useRef, useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import { LayoutGroup, motion } from 'framer-motion';
import { sortedNavItems, isNavItemActive, type NavItem } from '@/shared/app/nav';
import { AppLink } from '@/shared/ui/AppLink';
import { Button } from '@/shared/ui/Button';
import { MenuPanel, menuItemClass, useDismissable } from '@/shared/ui/Menu';
import { usePendingAccessRequestCount, usePendingAgentsCount } from '@/shared/hooks';
import { cn } from '@/shared/lib/utils';

const NAV_SPRING = { type: 'spring' as const, stiffness: 500, damping: 35 };

/**
 * Persistent pending-access-request count badge. Surfaces on the Dashboard nav
 * tab (where the approval queue lives) so the "N waiting" signal is visible at
 * every breakpoint — including when the Agent Rail is collapsed or hidden below
 * `xl`. Renders nothing when the queue is empty.
 */
function PendingRequestsBadge() {
	const { count, atLeast } = usePendingAccessRequestCount();
	if (count <= 0) return null;
	const label = atLeast ? `${count}+` : `${count}`;
	return (
		<span
			className="bg-warning/15 text-warning ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums"
			aria-label={`${label} access requests awaiting review`}
		>
			{label}
		</span>
	);
}

/**
 * Persistent pending-agents count badge on the Agents nav tab — mirrors
 * PendingRequestsBadge so a newly-registered agent awaiting approval is visible
 * without opening the Agents page or manually refreshing (#652). Renders nothing
 * when no agent is pending.
 */
function PendingAgentsBadge() {
	const { count, atLeast } = usePendingAgentsCount();
	if (count <= 0) return null;
	const label = atLeast ? `${count}+` : `${count}`;
	return (
		<span
			className="bg-warning/15 text-warning ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums"
			aria-label={`${label} agents awaiting approval`}
		>
			{label}
		</span>
	);
}

/** Renders the pending-count badge appropriate to a nav item, if any. */
function NavBadge({ navId }: { navId: string }) {
	if (navId === 'dashboard') return <PendingRequestsBadge />;
	if (navId === 'agents') return <PendingAgentsBadge />;
	return null;
}

/** Small fallback glyph for placeholder nav items that don't yet ship an icon. */
function NavGlyph({ item, className }: { item: NavItem; className?: string }) {
	const Icon = item.icon;
	if (Icon) return <Icon className={className} />;
	return (
		<span
			aria-hidden="true"
			className={cn('inline-block rounded-full bg-current/50', className)}
			style={{ width: '0.4rem', height: '0.4rem' }}
		/>
	);
}

function NavTab({ item, isActive }: { item: NavItem; isActive: boolean }) {
	return (
		<AppLink href={item.to} className="relative flex shrink-0 items-center">
			<span
				className={cn(
					'relative z-10 flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150',
					isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
				)}
			>
				<NavGlyph item={item} className="h-4 w-4 shrink-0" />
				<span>{item.label}</span>
				<NavBadge navId={item.id} />
			</span>
			{isActive && (
				<motion.span
					layoutId="activeNavTab"
					className="bg-muted absolute inset-0 -z-0 rounded-md"
					transition={NAV_SPRING}
					style={{ originY: '0px' }}
					aria-hidden="true"
				/>
			)}
		</AppLink>
	);
}

/**
 * Horizontal primary-nav tabs for the desktop top bar. Driven by the
 * append-only nav registry. Measures available width with a
 * `ResizeObserver` and pushes whatever doesn't fit into a "More ▾"
 * dropdown. A framer-motion `layoutId` slides the active-tab highlight
 * between tabs.
 */
export function NavTabs() {
	const { pathname } = useLocation();
	const items = sortedNavItems();
	const containerRef = useRef<HTMLDivElement>(null);
	const [visibleCount, setVisibleCount] = useState(items.length);
	const [overflowOpen, setOverflowOpen] = useState(false);
	const closeOverflow = useCallback(() => setOverflowOpen(false), []);
	const overflowRef = useDismissable<HTMLDivElement>(overflowOpen, closeOverflow);

	// Measure the available width of the row. The "More" button sits
	// immediately after the visible tabs, so the budget must reserve
	// space for it when overflow exists.
	useEffect(() => {
		function measure() {
			const container = containerRef.current;
			if (!container) return;
			const available = container.offsetWidth;
			// Approximate per-item widths (icon + label + padding).
			const itemWidths = items.map((item) => item.label.length * 7 + 60);
			const moreWidth = 80;
			let total = 0;
			let count = 0;
			for (let i = 0; i < itemWidths.length; i++) {
				const remaining = items.length - (count + 1);
				const extra = remaining > 0 ? moreWidth : 0;
				if (total + itemWidths[i] + extra > available) break;
				total += itemWidths[i];
				count++;
			}
			setVisibleCount(Math.max(1, count));
		}
		measure();
		const ro = new ResizeObserver(measure);
		if (containerRef.current) ro.observe(containerRef.current);
		return () => ro.disconnect();
		// `items` is derived from the static registry — stable across renders.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	const primary = items.slice(0, visibleCount);
	const overflow = items.slice(visibleCount);
	const overflowActive = overflow.some((item) => isNavItemActive(item, pathname));

	return (
		<LayoutGroup id="nav-tabs">
			<div ref={containerRef} className="flex items-center">
				<div className="flex min-w-0 items-center gap-0.5">
					{primary.map((item) => (
						<NavTab
							key={item.id}
							item={item}
							isActive={isNavItemActive(item, pathname)}
						/>
					))}
				</div>

				{overflow.length > 0 && (
					<div ref={overflowRef} className="relative shrink-0">
						<Button
							variant="ghost"
							size="sm"
							onClick={() => setOverflowOpen((o) => !o)}
							aria-haspopup="menu"
							aria-expanded={overflowOpen}
							className={cn(
								'shrink-0 gap-1.5 rounded-md',
								overflowActive
									? 'bg-muted text-foreground hover:bg-muted hover:text-foreground'
									: 'text-muted-foreground hover:text-foreground',
							)}
						>
							More
							<ChevronDown
								className={cn(
									'h-3.5 w-3.5 transition-transform duration-150',
									overflowOpen && 'rotate-180',
								)}
							/>
						</Button>

						{overflowOpen && (
							<MenuPanel align="left">
								{overflow.map((item) => {
									const isActive = isNavItemActive(item, pathname);
									return (
										<AppLink
											key={item.id}
											href={item.to}
											role="menuitem"
											onClick={closeOverflow}
											className={menuItemClass(isActive)}
										>
											<NavGlyph item={item} className="h-4 w-4 shrink-0" />
											{item.label}
											<NavBadge navId={item.id} />
										</AppLink>
									);
								})}
							</MenuPanel>
						)}
					</div>
				)}
			</div>
		</LayoutGroup>
	);
}
