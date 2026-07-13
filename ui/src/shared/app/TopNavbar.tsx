import { NavTabs } from '@/shared/app/NavTabs';
import { ROUTES } from '@/shared/app/routes';
import { UserMenu } from '@/shared/app/UserMenu';
import { AppLink } from '@/shared/ui/AppLink';
import { JenticLogo } from '@/shared/ui/Logo';

/**
 * Fixed top navigation bar: logo + desktop nav tabs (left) and the user
 * menu (right). The tab strip is hidden below `md`, where the
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

				{/* Right: user menu */}
				<div className="flex shrink-0 items-center gap-3 pl-4">
					<UserMenu />
				</div>
			</div>
		</header>
	);
}
