import {
	Activity,
	Bot,
	Compass,
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
 * The IA splits the discovery surface in two:
 *   - `Workspace` (`/workspace`) — what you've imported and have
 *     credentials for. Lists both APIs and your own Arazzo workflows.
 *   - `Discover` (`/discover`) — the public catalog you can pull from,
 *     including the catalog of workflows.
 *
 * Observability is unified under `Monitor` (`/monitor`), a single page
 * with three tabs (Overview, Execution Log, Jobs) that replaces the
 * previously-separate Traces and Async Jobs nav items. The old
 * `/traces` and `/jobs` routes redirect to `/monitor` (see App.tsx).
 *
 * Search has been folded into Discover; the `/workflows` list was
 * retired in favour of Workspace.
 */
export const NAV_ITEMS: NavItem[] = [
	{ href: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
	{ href: '/workspace', label: 'Workspace', icon: LayoutGrid },
	{ href: '/discover', label: 'Discover', icon: Compass },
	{ href: '/toolkits', label: 'Toolkits', icon: FolderOpen },
	{ href: '/credentials', label: 'Credentials', icon: KeyRound },
	{ href: '/agents', label: 'Agents', icon: Bot },
	{ href: '/monitor', label: 'Monitor', icon: Activity },
];
