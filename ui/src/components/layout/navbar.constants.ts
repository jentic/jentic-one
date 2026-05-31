import {
	Activity,
	Bot,
	Compass,
	Cog,
	FolderOpen,
	KeyRound,
	LayoutDashboard,
	LayoutGrid,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type NavItem = {
	href: string;
	label: string;
	icon: LucideIcon;
	/** Use exact matching (pathname === href) instead of startsWith */
	exact?: boolean;
};

/**
 * Primary navigation items in display order. `NavTabs` measures available
 * width with a `ResizeObserver` and pushes whatever doesn't fit into a
 * "More ▾" dropdown (`BottomNavbar` uses a fixed `TILE_LIMIT` instead).
 *
 * Order is deliberate — frequently-touched routes first, observability and
 * settings last.
 *
 * Icon choices mirror `jentic-webapp` where the concepts overlap
 * (Toolkits → FolderOpen, Traces → Activity). Mini-only routes
 * (Dashboard, Workspace, Credentials, Agents, Async Jobs) keep
 * semantically appropriate Lucide icons.
 *
 * Search has been removed as a standalone nav item; it is now the primary
 * interaction on the Discover surface (`/discover`). The IA splits the
 * surface in two:
 *   - `Workspace` (`/workspace`) — what you've imported and have
 *     credentials for. Lists both APIs and your own Arazzo workflows.
 *   - `Discover` (`/discover`) — the public catalog you can pull from,
 *     including the catalog of workflows.
 *
 * The standalone `/workflows` list was retired in favour of the
 * Workspace surface; `/workflows` now redirects to `/workspace`.
 * `/workflows/:slug` is unchanged — it's still where you inspect a
 * single workflow.
 */
export const NAV_ITEMS: NavItem[] = [
	{ href: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
	{ href: '/workspace', label: 'Workspace', icon: LayoutGrid },
	{ href: '/discover', label: 'Discover', icon: Compass },
	{ href: '/toolkits', label: 'Toolkits', icon: FolderOpen },
	{ href: '/credentials', label: 'Credentials', icon: KeyRound },
	{ href: '/agents', label: 'Agents', icon: Bot },
	{ href: '/traces', label: 'Traces', icon: Activity },
	{ href: '/jobs', label: 'Async Jobs', icon: Cog },
];
