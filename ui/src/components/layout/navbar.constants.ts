import {
	Activity,
	Bot,
	BookOpen,
	Cog,
	FolderOpen,
	KeyRound,
	LayoutDashboard,
	Search,
	Workflow,
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
 * Search last (Search has its own keyboard affordance elsewhere). This is
 * NOT the same order as the old sidebar; expect muscle-memory drift.
 *
 * Icon choices mirror `jentic-webapp` where the concepts overlap
 * (Toolkits → FolderOpen, Workflows → Workflow, Traces → Activity).
 * Mini-only routes (Dashboard, Credentials, Catalog, Agents, Async Jobs,
 * Search) keep semantically appropriate Lucide icons.
 */
export const NAV_ITEMS: NavItem[] = [
	{ href: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
	{ href: '/toolkits', label: 'Toolkits', icon: FolderOpen },
	{ href: '/credentials', label: 'Credentials', icon: KeyRound },
	{ href: '/catalog', label: 'API Catalog', icon: BookOpen },
	{ href: '/workflows', label: 'Workflows', icon: Workflow },
	{ href: '/agents', label: 'Agents', icon: Bot },
	{ href: '/traces', label: 'Traces', icon: Activity },
	{ href: '/jobs', label: 'Async Jobs', icon: Cog },
	{ href: '/search', label: 'Search', icon: Search },
];
