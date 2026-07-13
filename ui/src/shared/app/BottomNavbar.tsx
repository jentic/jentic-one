import { useCallback, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { MoreHorizontal, X } from 'lucide-react';
import { motion } from 'framer-motion';
import { sortedNavItems, isNavItemActive, type NavItem } from '@/shared/app/nav';
import { AppLink } from '@/shared/ui/AppLink';
import { Button } from '@/shared/ui/Button';
import { useDismissable } from '@/shared/ui/Menu';
import { usePendingAccessRequestCount, usePendingAgentsCount } from '@/shared/hooks';
import { cn } from '@/shared/lib/utils';

const BOTTOM_NAV_SPRING = { type: 'spring' as const, stiffness: 500, damping: 35 };

/** Number of tiles visible before the "More" tile appears. */
const TILE_LIMIT = 5;

/**
 * Pending-access-request count badge for the Dashboard tile, so the persistent
 * "N waiting" signal reaches mobile too (the Agent Rail is desktop-only). Pinned
 * to the tile's top-right; renders nothing when the queue is empty.
 */
function TilePendingBadge() {
	const { count, atLeast } = usePendingAccessRequestCount();
	if (count <= 0) return null;
	const label = atLeast ? `${count}+` : `${count}`;
	return (
		<span
			className="bg-warning text-background absolute top-1.5 right-1/2 z-20 inline-flex h-4 min-w-4 translate-x-4 items-center justify-center rounded-full px-1 text-[9px] font-bold tabular-nums"
			aria-label={`${label} access requests awaiting review`}
		>
			{label}
		</span>
	);
}

/**
 * Pending-agents count badge for the Agents tile — mirrors TilePendingBadge so a
 * newly-registered agent awaiting approval is visible on mobile too (#652).
 */
function TilePendingAgentsBadge() {
	const { count, atLeast } = usePendingAgentsCount();
	if (count <= 0) return null;
	const label = atLeast ? `${count}+` : `${count}`;
	return (
		<span
			className="bg-warning text-background absolute top-1.5 right-1/2 z-20 inline-flex h-4 min-w-4 translate-x-4 items-center justify-center rounded-full px-1 text-[9px] font-bold tabular-nums"
			aria-label={`${label} agents awaiting approval`}
		>
			{label}
		</span>
	);
}

/** Renders the pending-count tile badge appropriate to a nav item, if any. */
function TileBadge({ navId }: { navId: string }) {
	if (navId === 'dashboard') return <TilePendingBadge />;
	if (navId === 'agents') return <TilePendingAgentsBadge />;
	return null;
}

/** Small fallback glyph for placeholder nav items that don't yet ship an icon. */
function TileGlyph({ item, className }: { item: NavItem; className?: string }) {
	const Icon = item.icon;
	if (Icon) return <Icon className={className} />;
	return (
		<span
			aria-hidden="true"
			className={cn('inline-block rounded-full bg-current/50', className)}
			style={{ width: '0.55rem', height: '0.55rem' }}
		/>
	);
}

function BottomTile({
	item,
	isActive,
	onClick,
}: {
	item: NavItem;
	isActive: boolean;
	onClick?: () => void;
}) {
	return (
		<AppLink
			href={item.to}
			onClick={onClick}
			className="relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-2"
		>
			{item.id === 'dashboard' && <TilePendingBadge />}
			{item.id === 'agents' && <TilePendingAgentsBadge />}
			<span
				className={cn(
					'relative z-10 flex flex-col items-center gap-0.5 transition-colors duration-150',
					isActive ? 'text-foreground' : 'text-muted-foreground',
				)}
			>
				<TileGlyph item={item} className="h-5 w-5 shrink-0" />
				<span className="text-[10px] leading-none font-medium">{item.label}</span>
			</span>
			{isActive && (
				<motion.span
					layoutId="activeBottomNavTab"
					className="bg-muted absolute inset-x-1 inset-y-1 -z-0 rounded-lg"
					transition={BOTTOM_NAV_SPRING}
					aria-hidden="true"
				/>
			)}
		</AppLink>
	);
}

/**
 * Mobile primary nav: a fixed bottom tile bar (`md:hidden`) that mirrors
 * the desktop `NavTabs`. The first few items get tiles; the rest collapse
 * into a "More" sheet. Driven by the append-only nav registry.
 */
export function BottomNavbar() {
	const { pathname } = useLocation();
	const items = sortedNavItems();
	const [sheetOpen, setSheetOpen] = useState(false);
	const closeSheet = useCallback(() => setSheetOpen(false), []);
	// Wires Escape (and outside-click) on the sheet, matching the
	// dismiss behaviour of every other dropdown / overlay in the app.
	const sheetRef = useDismissable<HTMLDivElement>(sheetOpen, closeSheet);

	const primary = items.slice(0, TILE_LIMIT - 1);
	const overflow = items.slice(TILE_LIMIT - 1);
	const overflowActive = overflow.some((item) => isNavItemActive(item, pathname));

	return (
		<>
			<nav className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed right-0 bottom-0 left-0 z-50 border-t backdrop-blur md:hidden">
				<div className="flex h-16 items-stretch">
					{primary.map((item) => (
						<BottomTile
							key={item.id}
							item={item}
							isActive={isNavItemActive(item, pathname)}
						/>
					))}

					{overflow.length > 0 && (
						<Button
							variant="ghost"
							onClick={() => setSheetOpen(true)}
							aria-label="More navigation items"
							className={cn(
								'relative h-auto min-w-0 flex-1 flex-col gap-0.5 rounded-none py-2',
								overflowActive ? 'text-foreground' : 'text-muted-foreground',
							)}
						>
							{overflowActive && (
								<motion.span
									layoutId="activeBottomNavTab"
									className="bg-muted absolute inset-x-1 inset-y-1 -z-0 rounded-lg"
									transition={BOTTOM_NAV_SPRING}
									aria-hidden="true"
								/>
							)}
							<MoreHorizontal className="relative z-10 h-5 w-5 shrink-0" />
							<span className="relative z-10 text-[10px] leading-none font-medium">
								More
							</span>
						</Button>
					)}
				</div>
				{/* Safe-area spacer for devices with home indicator */}
				<div className="h-[env(safe-area-inset-bottom)]" />
			</nav>

			{sheetOpen && (
				<>
					<div
						className="fixed inset-0 z-50 bg-black/50 md:hidden"
						onClick={closeSheet}
						aria-hidden="true"
					/>
					<div
						ref={sheetRef}
						className="border-border bg-background fixed inset-x-0 bottom-0 z-50 rounded-t-xl border-t pb-[env(safe-area-inset-bottom)] md:hidden"
					>
						<div className="flex items-center justify-between px-4 py-3">
							<span className="text-foreground text-sm font-semibold">More</span>
							<Button
								variant="ghost"
								size="icon"
								onClick={closeSheet}
								aria-label="Close"
								className="text-muted-foreground hover:text-foreground"
							>
								<X className="h-5 w-5" />
							</Button>
						</div>
						<div className="grid grid-cols-3 gap-2 px-4 pb-6">
							{overflow.map((item) => {
								const isActive = isNavItemActive(item, pathname);
								return (
									<AppLink
										key={item.id}
										href={item.to}
										onClick={closeSheet}
										className={cn(
											'relative flex flex-col items-center gap-2 rounded-xl p-3 text-center transition-colors duration-150',
											isActive
												? 'bg-muted text-foreground'
												: 'text-muted-foreground hover:bg-muted hover:text-foreground',
										)}
									>
										<TileBadge navId={item.id} />
										<TileGlyph item={item} className="h-6 w-6" />
										<span className="text-xs leading-tight font-medium">
											{item.label}
										</span>
									</AppLink>
								);
							})}
						</div>
					</div>
				</>
			)}
		</>
	);
}
