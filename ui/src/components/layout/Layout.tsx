import { useLocation, Outlet } from 'react-router-dom';
import { BottomNavbar } from './BottomNavbar';
import { TopNavbar } from './TopNavbar';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { Toaster } from '@/components/ui/Toaster';

export function Layout() {
	const location = useLocation();

	return (
		<div className="bg-background min-h-dvh">
			<TopNavbar />

			{/*
			 * `pt-12` clears the fixed h-12 TopNavbar; `pb-20 md:pb-12` clears
			 * BottomNavbar on mobile. Horizontal padding is deliberately NOT
			 * applied here — every page owns its own gutter via `PageShell`
			 * (body) or `PageHeader` (full-bleed band), both pinned to the
			 * shared `--spacing-page-gutter` theme token (utility:
			 * `px-page-gutter`). This mirrors `jentic-webapp`.
			 */}
			<main className="pt-12 pb-20 md:pb-12">
				<ErrorBoundary resetKey={location.pathname}>
					<Outlet />
				</ErrorBoundary>
			</main>

			<BottomNavbar />
			<Toaster />
		</div>
	);
}
