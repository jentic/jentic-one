import { useRef } from 'react';
import { useLocation, Outlet } from 'react-router';
import { BottomNavbar } from '@/shared/app/BottomNavbar';
import { TopNavbar } from '@/shared/app/TopNavbar';
import { UpdateBanner } from '@/shared/app/UpdateBanner';
import { AgentRail } from '@/shared/app/rail/AgentRail';
import { ShellActivityEffects, isRailHiddenOn } from '@/shared/app/rail/ShellActivityEffects';
import { ToastRegion } from '@/shared/app/ToastRegion';
import { useLinkViewTransitions } from '@/shared/app/viewTransitions';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';
import { useCoversRightEdge } from '@/shared/ui/rightEdge';
import { AgentStreamProvider } from '@/shared/lib/agentStream';

/**
 * Authenticated app shell:
 *
 *  - a fixed `h-12` `TopNavbar` (logo + desktop nav tabs + user menu),
 *  - a fixed mobile `BottomNavbar` (`md:hidden`),
 *  - a full-bleed `<main>` that owns NO horizontal padding,
 *  - a collapsible **Activity** rail on the right at `xl+` (the live platform
 *    event feed — see `shared/lib/agentStream`), collapsed to a strip by
 *    default; below `xl` the same rail body opens as a drawer from the
 *    TopNavbar. Not mounted on Monitor, whose Live activity panel already IS the
 *    activity stream (`isRailHiddenOn`),
 *  - the `ToastRegion`, bottom-right beside the rail or an open sheet.
 *
 * The body below the fixed navbar is a flex row: `<main>` takes the remaining
 * width (`flex-1 min-w-0`, still full-bleed — no horizontal padding here; pages
 * own their gutter via `PageShell`/`PageHeader`) and the rail sits beside it at
 * `xl+`, wrapped in a `sticky top-12 h-[calc(100dvh-3rem)] self-start` container
 * so it stays pinned under the navbar and its feed scrolls internally (keeping
 * the "Open full log in Monitor" footer always visible). Below `xl` the rail is hidden, so
 * `<main>` spans the full width exactly as before. `pt-12` (on `<main>`) clears
 * the fixed TopNavbar; `pb-20 md:pb-12` clears the mobile BottomNavbar.
 *
 * In-app link clicks run as view transitions (`useLinkViewTransitions`): pages
 * cross-fade and the activity stream morphs between wherever it's docked —
 * the rail here, the panel (and full log) on Monitor.
 *
 * Everything is wrapped in `AgentStreamProvider` so the rail, the drawer and
 * the ToastHost share one live event stream. Rendered behind AuthGuard, so `user` is always
 * present downstream.
 */
export function Layout() {
	const location = useLocation();
	const railRef = useRef<HTMLDivElement>(null);
	const showRail = !isRailHiddenOn(location.pathname);
	// The rail's column is `hidden` below `xl`, where it measures 0 and takes no room.
	useCoversRightEdge(railRef, showRail);
	useLinkViewTransitions();

	return (
		<AgentStreamProvider>
			<ShellActivityEffects />
			<div className="bg-background text-foreground min-h-dvh">
				<TopNavbar showActivity={showRail} />

				<div className="flex min-h-dvh">
					<main className="min-w-0 flex-1 pt-12 pb-20 md:pb-12">
						<UpdateBanner />
						<ErrorBoundary resetKey={location.pathname}>
							<Outlet />
						</ErrorBoundary>
					</main>

					{/*
					 * Sticky under the fixed h-12 TopNavbar with a viewport-minus-navbar
					 * height so the rail stays in view and its feed scrolls internally
					 * (RailFeed is `overflow-y-auto` and needs a bounded height). Without
					 * this cap the aside would stretch to the full row height on long
					 * pages and push the RailFooter (the Monitor link) below
					 * the fold. `self-start` pins it to the top instead of stretching.
					 */}
					{showRail && (
						<div
							ref={railRef}
							className="sticky top-12 hidden h-[calc(100dvh-3rem)] shrink-0 self-start xl:flex"
						>
							<AgentRail />
						</div>
					)}
				</div>

				<BottomNavbar />
				<ToastRegion />
			</div>
		</AgentStreamProvider>
	);
}
