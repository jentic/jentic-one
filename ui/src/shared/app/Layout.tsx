import { useLocation, Outlet } from 'react-router-dom';
import { BottomNavbar } from '@/shared/app/BottomNavbar';
import { TopNavbar } from '@/shared/app/TopNavbar';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ToastHost } from '@/shared/app/rail/ToastHost';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { Toaster } from '@/shared/ui/Toaster';
import { AgentStreamProvider } from '@/shared/lib/agentStream';

/**
 * Authenticated app shell. Ported from jentic-mini's `Layout`:
 *
 *  - a fixed `h-12` `TopNavbar` (logo + desktop nav tabs + user menu),
 *  - a fixed mobile `BottomNavbar` (`md:hidden`),
 *  - a full-bleed `<main>` that owns NO horizontal padding,
 *  - a persistent, collapsible **Agent Rail** + `ToastHost` mounted on the
 *    right at `xl+` (the live platform event feed — see `shared/lib/agentStream`).
 *
 * The body below the fixed navbar is a flex row: `<main>` takes the remaining
 * width (`flex-1 min-w-0`, still full-bleed — no horizontal padding here; pages
 * own their gutter via `PageShell`/`PageHeader`) and the rail sits beside it at
 * `xl+`, wrapped in a `sticky top-12 h-[calc(100dvh-3rem)] self-start` container
 * so it stays pinned under the navbar and its feed scrolls internally (keeping
 * the RailFooter controls always visible). Below `xl` the rail is hidden, so
 * `<main>` spans the full width exactly as before. `pt-12` (on `<main>`) clears
 * the fixed TopNavbar; `pb-20 md:pb-12` clears the mobile BottomNavbar.
 *
 * Everything is wrapped in `AgentStreamProvider` so the rail and the ToastHost
 * share one live event stream. Rendered behind AuthGuard, so `user` is always
 * present downstream.
 */
export function Layout() {
	const location = useLocation();

	return (
		<AgentStreamProvider>
			<div className="bg-background text-foreground min-h-dvh">
				<TopNavbar />

				<div className="flex min-h-dvh">
					<main className="min-w-0 flex-1 pt-12 pb-20 md:pb-12">
						<ErrorBoundary resetKey={location.pathname}>
							<Outlet />
						</ErrorBoundary>
					</main>

					{/*
					 * Sticky under the fixed h-12 TopNavbar with a viewport-minus-navbar
					 * height so the rail stays in view and its feed scrolls internally
					 * (RailFeed is `overflow-y-auto` and needs a bounded height). Without
					 * this cap the aside would stretch to the full row height on long
					 * pages and push the RailFooter (toast scope + audio toggle) below
					 * the fold. `self-start` pins it to the top instead of stretching.
					 */}
					<div className="sticky top-12 hidden h-[calc(100dvh-3rem)] shrink-0 self-start xl:flex">
						<AgentRail />
					</div>
				</div>

				<BottomNavbar />
				<Toaster />
				<ToastHost />
			</div>
		</AgentStreamProvider>
	);
}
