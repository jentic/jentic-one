import type { ComponentType } from 'react';
import {
	Compass,
	Boxes,
	Bot,
	LayoutDashboard,
	LayoutGrid,
	KeyRound,
	Activity,
	Gauge,
	BookText,
} from 'lucide-react';

/**
 * A primary-navigation entry. `order` (not array position) controls placement,
 * so feature PRs never need to reorder existing lines.
 *
 * `icon` is an optional component (e.g. a lucide-react icon) the feature PR can
 * supply; the layout renders a fallback glyph when it's absent.
 */
export interface NavItem {
	id: string;
	label: string;
	/** Root-relative client path (basename `/app` is prepended by the router). */
	to: string;
	order: number;
	icon?: ComponentType<{ className?: string }>;
}

/**
 * Primary nav registry — APPEND-ONLY / ONE-LINE-PER-MODULE.
 *
 * The 7 feature slots below are PLACEHOLDERS so the human sees the full shell
 * now. Each feature PR REPLACES its own single line (swapping the placeholder
 * for the real entry, e.g. adding its `icon`), which keeps edits to exactly one
 * line per module and collision-free across parallel PRs. Do NOT reorder; bump
 * `order` if you need to move an item.
 *
 * `to` values are root-relative to the router basename (`/app`); the Dashboard
 * is the basename index (`/`, → `/app`).
 */
export const navItems: NavItem[] = [
	{ id: 'dashboard', label: 'Dashboard', to: '/', order: 10, icon: LayoutDashboard },
	{ id: 'discover', label: 'Discover', to: '/discover', order: 20, icon: Compass },
	{ id: 'workspace', label: 'Workspace', to: '/workspace', order: 30, icon: LayoutGrid },
	{ id: 'toolkits', label: 'Toolkits', to: '/toolkits', order: 40, icon: Boxes },
	{ id: 'credentials', label: 'Credentials', to: '/credentials', order: 50, icon: KeyRound },
	{ id: 'agents', label: 'Agents', to: '/agents', order: 60, icon: Bot },
	{ id: 'monitor', label: 'Monitor', to: '/monitor', order: 70, icon: Activity },
	{ id: 'llm-proxy', label: 'LLM Proxy', to: '/llm-proxy', order: 75, icon: Gauge },
	{ id: 'docs', label: 'API Reference', to: '/docs', order: 80, icon: BookText },
];

/** Nav items sorted for rendering. */
export function sortedNavItems(): NavItem[] {
	return [...navItems].sort((a, b) => a.order - b.order);
}

/**
 * Whether a nav item is "active" for the given pathname. `pathname` is the
 * router's basename-relative location (react-router strips `/app`), so the
 * Dashboard root is `/`; it matches exactly while every other item matches by
 * prefix so nested routes (e.g. `/discover/123`) keep their tab highlighted.
 */
export function isNavItemActive(item: NavItem, pathname: string): boolean {
	if (item.to === '/') return pathname === '/';
	return pathname === item.to || pathname.startsWith(`${item.to}/`);
}
