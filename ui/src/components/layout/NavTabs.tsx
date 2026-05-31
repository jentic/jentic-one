import { useRef, useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import { LayoutGroup, motion } from 'framer-motion';
import { NAV_ITEMS } from './navbar.constants';
import type { NavItem } from './navbar.constants';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { MenuPanel, menuItemClass, useDismissable } from '@/components/ui/Menu';
import { cn } from '@/lib/utils';

const NAV_SPRING = { type: 'spring' as const, stiffness: 500, damping: 35 };

function NavTab({ item, isActive }: { item: NavItem; isActive: boolean }) {
	const Icon = item.icon;
	return (
		<AppLink href={item.href} className="relative flex shrink-0 items-center">
			<span
				className={cn(
					'relative z-10 flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-150',
					isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
				)}
			>
				<Icon className="h-4 w-4 shrink-0" />
				<span>{item.label}</span>
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

export function NavTabs() {
	const { pathname } = useLocation();
	const containerRef = useRef<HTMLDivElement>(null);
	const [visibleCount, setVisibleCount] = useState(NAV_ITEMS.length);
	const [overflowOpen, setOverflowOpen] = useState(false);
	const closeOverflow = useCallback(() => setOverflowOpen(false), []);
	const overflowRef = useDismissable<HTMLDivElement>(overflowOpen, closeOverflow);

	// Measure the available width of the OUTER row (we attach `containerRef`
	// to the outer container below). The inner strip shrink-wraps to visible
	// tabs, and the "More" button sits immediately after them, so the budget
	// must reserve space for it when overflow exists.
	useEffect(() => {
		function measure() {
			const container = containerRef.current;
			if (!container) return;
			const available = container.offsetWidth;
			// Approximate per-item widths (icon + label + padding).
			const itemWidths = NAV_ITEMS.map((item) => item.label.length * 7 + 60);
			const moreWidth = 80;
			let total = 0;
			let count = 0;
			for (let i = 0; i < itemWidths.length; i++) {
				const remaining = NAV_ITEMS.length - (count + 1);
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
	}, []);

	const primary = NAV_ITEMS.slice(0, visibleCount);
	const overflow = NAV_ITEMS.slice(visibleCount);
	const overflowActive = overflow.some((item) =>
		item.exact ? pathname === item.href : pathname.startsWith(item.href),
	);

	return (
		<LayoutGroup id="nav-tabs">
			<div ref={containerRef} className="flex items-center">
				<div className="flex min-w-0 items-center gap-0.5">
					{primary.map((item) => {
						const isActive = item.exact
							? pathname === item.href
							: pathname.startsWith(item.href);
						return <NavTab key={item.href} item={item} isActive={isActive} />;
					})}
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
									const isActive = item.exact
										? pathname === item.href
										: pathname.startsWith(item.href);
									const Icon = item.icon;
									return (
										<AppLink
											key={item.href}
											href={item.href}
											role="menuitem"
											onClick={closeOverflow}
											className={menuItemClass(isActive)}
										>
											<Icon className="h-4 w-4 shrink-0" />
											{item.label}
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
