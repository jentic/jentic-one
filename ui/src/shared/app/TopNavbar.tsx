import { useLocation } from 'react-router-dom';
import { BookText } from 'lucide-react';
import { NavTabs } from '@/shared/app/NavTabs';
import { ROUTES } from '@/shared/app/routes';
import { UserMenu } from '@/shared/app/UserMenu';
import { AppLink } from '@/shared/ui/AppLink';
import { JenticLogo } from '@/shared/ui/Logo';
import { cn } from '@/shared/lib/utils';

/**
 * Standalone docs entry — reference material, not a product destination, so it
 * lives apart from the primary tabs, next to the user menu. Icon-only with a
 * text label from `md` up; visible at every breakpoint (the mobile BottomNavbar
 * deliberately omits it).
 */
function DocsLink() {
	const { pathname } = useLocation();
	const isActive = pathname === ROUTES.docs || pathname.startsWith(`${ROUTES.docs}/`);
	return (
		<AppLink
			href={ROUTES.docs}
			aria-label="API Reference"
			title="API Reference"
			aria-current={isActive ? 'page' : undefined}
			className={cn(
				'flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2 text-sm font-medium transition-colors duration-150',
				isActive
					? 'bg-muted text-foreground'
					: 'text-muted-foreground hover:bg-muted hover:text-foreground',
			)}
		>
			<BookText className="h-4 w-4 shrink-0" aria-hidden="true" />
			<span className="hidden md:inline">Docs</span>
		</AppLink>
	);
}

/**
 * Fixed top navigation bar: logo + desktop nav tabs (left) and the docs link +
 * user menu (right). The tab strip is hidden below `md`, where the
 * `BottomNavbar` takes over.
 */
export function TopNavbar() {
	return (
		<header
			data-top-navbar
			className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-0 right-0 left-0 z-50 border-b backdrop-blur"
		>
			<div className="flex h-12 items-center justify-between px-4">
				{/* Left: logo + separator + nav tabs */}
				<div className="flex min-w-0 flex-1 items-center gap-3">
					<AppLink
						href={ROUTES.app}
						className="flex shrink-0 items-center"
						aria-label="Jentic One home"
					>
						<JenticLogo />
					</AppLink>

					<div
						className="bg-border hidden h-4 w-px shrink-0 md:block"
						aria-hidden="true"
					/>

					{/* Desktop tabs — the landmark stays in the tree at all
					 * breakpoints (so the "Primary" nav is always discoverable);
					 * only the tab strip itself collapses on mobile, where the
					 * BottomNavbar takes over. */}
					<nav aria-label="Primary" className="min-w-0 flex-1">
						<div className="hidden md:block">
							<NavTabs />
						</div>
					</nav>
				</div>

				{/* Right: docs + user menu */}
				<div className="flex shrink-0 items-center gap-2 pl-4">
					<DocsLink />
					<div className="bg-border h-4 w-px shrink-0" aria-hidden="true" />
					<UserMenu />
				</div>
			</div>
		</header>
	);
}
