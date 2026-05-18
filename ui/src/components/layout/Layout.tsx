import { useLocation, Outlet } from 'react-router-dom';
import { BottomNavbar } from './BottomNavbar';
import { TopNavbar } from './TopNavbar';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';

export function Layout() {
	const location = useLocation();

	return (
		<div className="bg-background min-h-dvh">
			<TopNavbar />

			{/* pt-12 clears the fixed h-12 TopNavbar; pb-20 md:pb-12 clears BottomNavbar on mobile */}
			<main className="pt-12 pb-20 md:pb-12">
				<div className="p-4 md:p-6">
					<ErrorBoundary resetKey={location.pathname}>
						<Outlet />
					</ErrorBoundary>
				</div>
			</main>

			<BottomNavbar />
		</div>
	);
}
