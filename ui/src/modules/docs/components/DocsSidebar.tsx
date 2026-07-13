/**
 * DocsSidebar — the sticky left rail for the developer docs.
 *
 * Renders the grouped section list (from `DOCS_NAV`) as anchor links, with the
 * active section (from scroll-spy) highlighted. Clicking smooth-scrolls to the
 * section and updates the URL hash. On narrow screens the rail collapses to a
 * horizontal scroller above the content (handled by the parent's layout).
 */
import { useEffect, useRef } from 'react';
import { DOCS_NAV } from '@/modules/docs/lib/nav';
import type { DocsSubSection } from '@/modules/docs/lib/nav';
import { cn } from '@/shared/lib/utils';

export interface DocsSidebarProps {
	activeId: string | null;
	/** The currently in-view sub-anchor (e.g. a CLI binary), for highlighting. */
	activeSubId?: string | null;
	/**
	 * Runtime-computed children to render under a section, keyed by section id.
	 * Used for the API reference, whose sub-groups come from the spec (its
	 * x-tagGroups) and so can't live in the static nav. Merged over (replacing)
	 * any static children for that section.
	 */
	extraChildren?: Record<string, DocsSubSection[]>;
	onNavigate: (id: string) => void;
}

export function DocsSidebar({
	activeId,
	activeSubId,
	extraChildren,
	onNavigate,
}: DocsSidebarProps) {
	const navRef = useRef<HTMLElement | null>(null);

	// Keep the highlighted entry in view as the reader scrolls the document. The
	// rail is its own scroll container, so without this the active item drifts
	// out of sight (and, scrolling up, ends up tucked under the sticky search
	// bar). Prefer the sub-anchor (more specific) when one is active. We only
	// nudge when the entry is actually clipped, and use 'nearest' so an item
	// that's merely a little high isn't yanked to the very top edge.
	useEffect(() => {
		const nav = navRef.current;
		if (!nav) return;
		const target = activeSubId ?? activeId;
		if (!target) return;
		const el = nav.querySelector<HTMLElement>(`[data-nav-id="${CSS.escape(target)}"]`);
		if (!el) return;
		const navBox = nav.getBoundingClientRect();
		const elBox = el.getBoundingClientRect();
		// Leave a small margin so the active row never hugs the top/bottom edge.
		const margin = 12;
		if (elBox.top < navBox.top + margin || elBox.bottom > navBox.bottom - margin) {
			el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		}
	}, [activeId, activeSubId]);

	return (
		<nav ref={navRef} aria-label="Documentation sections" className="space-y-5">
			{DOCS_NAV.map((group) => (
				<div key={group.title}>
					<p className="text-foreground/40 mb-1.5 px-2 text-[11px] font-semibold tracking-wider uppercase">
						{group.title}
					</p>
					<ul className="space-y-0.5">
						{group.sections.map((section) => {
							const active = section.id === activeId;
							const children = extraChildren?.[section.id] ?? section.children;
							return (
								<li key={section.id}>
									<a
										href={`#${section.id}`}
										data-nav-id={section.id}
										aria-current={active ? 'location' : undefined}
										onClick={(e) => {
											e.preventDefault();
											onNavigate(section.id);
										}}
										className={cn(
											'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
											active
												? 'bg-primary/10 text-primary font-medium'
												: 'text-foreground/65 hover:bg-muted hover:text-foreground',
										)}
									>
										<section.icon
											className="h-4 w-4 shrink-0"
											aria-hidden="true"
										/>
										{section.label}
									</a>

									{/* Nested entries (CLI binaries / API tag-groups) are ALWAYS
									    shown so the reader can jump straight to a sub-target from
									    anywhere. The in-view sub-anchor (scroll-spy) is highlighted. */}
									{children && (
										<ul className="border-border/60 mt-0.5 ml-[1.05rem] space-y-0.5 border-l pl-2">
											{children.map((child) => {
												const childActive = child.id === activeSubId;
												return (
													<li key={child.id}>
														<a
															href={`#${child.id}`}
															data-nav-id={child.id}
															aria-current={
																childActive ? 'location' : undefined
															}
															onClick={(e) => {
																e.preventDefault();
																onNavigate(child.id);
															}}
															className={cn(
																'block rounded-md px-2 py-1 text-[13px] transition-colors',
																child.mono && 'font-mono',
																childActive
																	? 'text-primary font-medium'
																	: 'text-foreground/55 hover:bg-muted hover:text-foreground',
															)}
														>
															{child.label}
														</a>
													</li>
												);
											})}
										</ul>
									)}
								</li>
							);
						})}
					</ul>
				</div>
			))}
		</nav>
	);
}
