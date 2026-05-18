import { useCallback, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { MoreHorizontal, X } from 'lucide-react';
import { motion } from 'framer-motion';
import { NAV_ITEMS } from './navbar.constants';
import type { NavItem } from './navbar.constants';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { useDismissable } from '@/components/ui/Menu';
import { cn } from '@/lib/utils';

const BOTTOM_NAV_SPRING = { type: 'spring' as const, stiffness: 500, damping: 35 };

/** Number of tiles visible before the "More" tile appears. */
const TILE_LIMIT = 5;

function BottomTile({
	item,
	isActive,
	onClick,
}: {
	item: NavItem;
	isActive: boolean;
	onClick?: () => void;
}) {
	const Icon = item.icon;
	return (
		<AppLink
			href={item.href}
			onClick={onClick}
			className="relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-2"
		>
			<span
				className={cn(
					'relative z-10 flex flex-col items-center gap-0.5 transition-colors duration-150',
					isActive ? 'text-foreground' : 'text-muted-foreground',
				)}
			>
				<Icon className="h-5 w-5 shrink-0" />
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

export function BottomNavbar() {
	const { pathname } = useLocation();
	const [sheetOpen, setSheetOpen] = useState(false);
	const closeSheet = useCallback(() => setSheetOpen(false), []);
	// Wires Escape (and outside-click) on the sheet, matching the
	// dismiss behaviour of every other dropdown / overlay in the app.
	const sheetRef = useDismissable<HTMLDivElement>(sheetOpen, closeSheet);

	const primary = NAV_ITEMS.slice(0, TILE_LIMIT - 1);
	const overflow = NAV_ITEMS.slice(TILE_LIMIT - 1);
	const overflowActive = overflow.some((item) =>
		item.exact ? pathname === item.href : pathname.startsWith(item.href),
	);

	return (
		<>
			<nav className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed right-0 bottom-0 left-0 z-50 border-t backdrop-blur md:hidden">
				<div className="flex h-16 items-stretch">
					{primary.map((item) => {
						const isActive = item.exact
							? pathname === item.href
							: pathname.startsWith(item.href);
						return <BottomTile key={item.href} item={item} isActive={isActive} />;
					})}

					{/* More tile */}
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
				</div>
				{/* Safe-area spacer for devices with home indicator */}
				<div className="h-[env(safe-area-inset-bottom)]" />
			</nav>

			{/* Overflow sheet */}
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
								const isActive = item.exact
									? pathname === item.href
									: pathname.startsWith(item.href);
								const Icon = item.icon;
								return (
									<AppLink
										key={item.href}
										href={item.href}
										onClick={closeSheet}
										className={cn(
											'flex flex-col items-center gap-2 rounded-xl p-3 text-center transition-colors duration-150',
											isActive
												? 'bg-muted text-foreground'
												: 'text-muted-foreground hover:bg-muted hover:text-foreground',
										)}
									>
										<Icon className="h-6 w-6" />
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
