/**
 * Docs navigation model — the ordered, grouped sections of the developer docs.
 *
 * The docs page is a single scrolling document with a sticky left rail; each
 * entry here maps to a section rendered in `DocsPage` (anchored by `id`) and a
 * link in `DocsSidebar`. Keeping this as data (not JSX) lets the layout,
 * scroll-spy, and the page body stay in lock-step from one source of truth.
 */
import type { LucideIcon } from 'lucide-react';
import {
	Rocket,
	Download,
	Terminal,
	Boxes,
	ShieldCheck,
	SquareTerminal,
	BookOpen,
	Network,
} from 'lucide-react';

export interface DocsSection {
	/** Anchor id — used for the URL hash, scroll target, and scroll-spy. */
	id: string;
	/** Sidebar + heading label. */
	label: string;
	icon: LucideIcon;
	/** Optional nested entries (e.g. CLI binaries), rendered indented in the rail. */
	children?: DocsSubSection[];
}

/** A nested nav entry under a section (e.g. a CLI binary). Jumps to an anchor
 *  but does NOT participate in top-level scroll-spy. */
export interface DocsSubSection {
	/** Anchor id of the in-page target. */
	id: string;
	label: string;
	/** Render the label in a monospace font (e.g. CLI binaries). Default false. */
	mono?: boolean;
}

export interface DocsNavGroup {
	title: string;
	sections: DocsSection[];
}

export const DOCS_NAV: DocsNavGroup[] = [
	{
		title: 'Get started',
		sections: [
			{ id: 'overview', label: 'Overview', icon: Rocket },
			{ id: 'installation', label: 'Installation', icon: Download },
			{ id: 'quickstart', label: 'Quickstart', icon: Terminal },
		],
	},
	{
		title: 'Concepts',
		sections: [
			{ id: 'architecture', label: 'Architecture', icon: Boxes },
			{ id: 'permissions', label: 'Permissions', icon: ShieldCheck },
		],
	},
	{
		title: 'Reference',
		sections: [
			{
				id: 'cli',
				label: 'CLI',
				icon: SquareTerminal,
				children: [
					{ id: 'cli-jenticctl', label: 'jenticctl', mono: true },
					{ id: 'cli-jentic', label: 'jentic', mono: true },
				],
			},
			{ id: 'api', label: 'API reference', icon: BookOpen },
			{ id: 'broker', label: 'Broker API', icon: Network },
		],
	},
];

/** Flat, ordered list of every section (for scroll-spy + iteration). */
export const DOCS_SECTIONS: DocsSection[] = DOCS_NAV.flatMap((g) => g.sections);
