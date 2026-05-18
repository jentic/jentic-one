import { NavTabs } from './NavTabs';
import { UserMenu } from './UserMenu';
import { AppLink } from '@/components/ui/AppLink';
import { JenticLogo } from '@/components/ui/Logo';
import { usePendingRequests } from '@/hooks/usePendingRequests';

export function TopNavbar() {
	const { data: pendingRequests } = usePendingRequests();

	return (
		<header
			data-top-navbar
			className="border-border bg-background/95 supports-[backdrop-filter]:bg-background/60 fixed top-0 right-0 left-0 z-50 border-b backdrop-blur"
		>
			<div className="flex h-12 items-center justify-between px-4">
				{/* Left: logo + separator + nav tabs */}
				<div className="flex min-w-0 flex-1 items-center gap-3">
					<AppLink
						href="/"
						className="flex shrink-0 items-center"
						aria-label="Jentic Mini home"
					>
						<JenticLogo />
					</AppLink>

					{/* Separator */}
					<div
						className="bg-border hidden h-4 w-px shrink-0 md:block"
						aria-hidden="true"
					/>

					{/* Desktop tabs — hidden on mobile */}
					<div className="hidden min-w-0 flex-1 md:block">
						<NavTabs />
					</div>
				</div>

				{/* Right: pending requests pill + user menu */}
				<div className="flex shrink-0 items-center gap-3 pl-4">
					{pendingRequests && pendingRequests.length > 0 && (
						<AppLink
							href="/toolkits"
							className="bg-danger/10 text-danger border-danger/30 hover:bg-danger/20 flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-semibold transition-colors duration-150"
						>
							<span className="bg-danger h-2 w-2 animate-pulse rounded-full" />
							{pendingRequests.length}{' '}
							{pendingRequests.length === 1 ? 'Pending Request' : 'Pending Requests'}
						</AppLink>
					)}

					<UserMenu />
				</div>
			</div>
		</header>
	);
}
