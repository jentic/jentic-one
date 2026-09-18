/**
 * AgentStrip — the agent selector on the flat Agents surface. Selecting a tab
 * switches the surface in place (no drill-down); the page persists the
 * selection in `?agent=` so a selected agent stays linkable.
 *
 * Grammar: quiet text tabs inside one low-contrast rail — the selected tab is
 * a subtle filled slab, not a loud chip — each carrying a muted count of the
 * APIs that agent reaches. A status dot is drawn only for a NON-ACTIVE agent
 * (active is the quiet default, so the dot means "look at this one"), plus a
 * "· N to set up" hint when bound credentials are still waiting on an OAuth
 * sign-in.
 *
 * It is a real `role="tablist"` with roving tabindex: arrow keys move the
 * selection (selection follows focus, the standard tabs pattern — moving
 * selection is never destructive). The filter input lives in the PAGE HEADER
 * beside the other page-level controls, so it is passed in controlled; this
 * component only applies it.
 *
 * Pending agents sit as their OWN GROUP at the head of the strip (D15): a
 * "Waiting" micro-label opens the group and a hairline divider closes it, so
 * "these are waiting on you" reads at a glance instead of being merely
 * sorted-first. The group is visual only — the tabs stay direct children of
 * the one tablist (dividers are aria-hidden), so the roving tabindex and the
 * filter traverse the whole strip unchanged; screen readers hear the state
 * from the sr-only "awaiting approval" suffix on each pending tab.
 *
 * The rail is ONE row at every width: it scrolls horizontally (snapped, no
 * mid-word truncation) rather than wrapping, so a fleet of any size costs the
 * same vertical space and the tiles below never shift down as the fleet grows.
 * A fade is drawn only on an end that actually has tabs behind it, so the fade
 * reads as "scroll this way" instead of decorating a rail that already fits.
 *
 * It also STICKS under the fixed `h-12` TopNavbar (`sticky top-12`, the app's
 * established gutter-bleeding sticky bar, growing a hairline while it is
 * pinned): the selection stays reachable while scrolling a long tile grid,
 * where the page header and its actions scroll away.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { STATUS_DOT } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { AgentEntity } from '@/modules/agents/api';

/** Width of the scroll-affordance fade at either end of the rail. */
const FADE = '1.5rem';

/** Where the rail pins: directly under the fixed `h-12` TopNavbar. Kept in sync
 * with the `sticky top-12` class below, which the pinned-state observer reads. */
const STICKY_TOP = 48;

/** The mask that fades whichever end still has tabs behind it. */
function edgeMask(start: boolean, end: boolean): string | undefined {
	if (start && end)
		return `linear-gradient(to right, transparent, black ${FADE}, black calc(100% - ${FADE}), transparent)`;
	if (end) return `linear-gradient(to right, black calc(100% - ${FADE}), transparent)`;
	if (start) return `linear-gradient(to right, transparent, black ${FADE})`;
	return undefined;
}

interface AgentStripProps {
	agents: AgentEntity[];
	selectedId: string | null;
	onSelect: (id: string) => void;
	/** Per-agent count of bound credentials that are not usable yet. */
	setupGaps: ReadonlyMap<string, number>;
	/** Per-agent count of reachable APIs. A missing key claims nothing — the
	 * tab shows no count rather than a figure the join can't prove. */
	apiCounts: ReadonlyMap<string, number>;
	/** The page header's filter text (this component is the consumer, not the
	 * owner — the input sits with the page-level controls). */
	filter: string;
}

export function AgentStrip({
	agents,
	selectedId,
	onSelect,
	setupGaps,
	apiCounts,
	filter,
}: AgentStripProps) {
	const tabRefs = useRef(new Map<string, HTMLButtonElement>());
	const scrollerRef = useRef<HTMLDivElement | null>(null);
	const barRef = useRef<HTMLDivElement | null>(null);
	// Which ends still hold tabs out of view — the only thing the fade claims.
	const [edges, setEdges] = useState({ start: false, end: false });

	const visible = useMemo(() => {
		const q = filter.trim().toLowerCase();
		const matched = q
			? agents.filter(
					(a) => a.name.toLowerCase().includes(q) || a.id.toLowerCase().includes(q),
				)
			: agents;
		// D15 grouping: pending tabs always lead. The parent already sorts
		// decisions-first, but the split is re-asserted here so the DOM order,
		// the visual group and the arrow-key traversal can never disagree.
		return {
			pending: matched.filter((a) => a.status === 'pending'),
			rest: matched.filter((a) => a.status !== 'pending'),
		};
	}, [agents, filter]);
	const ordered = useMemo(() => [...visible.pending, ...visible.rest], [visible]);

	const syncEdges = useCallback(() => {
		const scroller = scrollerRef.current;
		if (!scroller) return;
		// 1px of slack: fractional layout widths make an unscrollable rail
		// report a sub-pixel overflow, which would fade an end for no reason.
		const max = scroller.scrollWidth - scroller.clientWidth;
		const next = { start: scroller.scrollLeft > 1, end: scroller.scrollLeft < max - 1 };
		setEdges((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
	}, []);

	// The rail's overflow changes on scroll, on resize AND as the fleet or the
	// filter changes the tab count, so all three re-measure.
	useEffect(() => {
		syncEdges();
		const scroller = scrollerRef.current;
		if (!scroller || typeof ResizeObserver === 'undefined') return;
		const obs = new ResizeObserver(syncEdges);
		obs.observe(scroller);
		return () => obs.disconnect();
	}, [syncEdges, ordered.length]);

	// Keep the selected tab in view: selection also arrives from elsewhere (the
	// approval banner's Review, an `?agent=` deep link), where nothing focuses
	// the tab. Scrolls the rail ONLY — never an ancestor, so the page keeps its
	// own scroll position.
	useEffect(() => {
		const scroller = scrollerRef.current;
		const tab = selectedId ? tabRefs.current.get(selectedId) : null;
		if (!scroller || !tab) return;
		const rail = scroller.getBoundingClientRect();
		const box = tab.getBoundingClientRect();
		const pad = 24;
		if (box.left < rail.left + pad) scroller.scrollLeft -= rail.left + pad - box.left;
		else if (box.right > rail.right - pad)
			scroller.scrollLeft += box.right - (rail.right - pad);
	}, [selectedId, ordered.length]);

	// Hairline once the rail is actually pinned, so a stuck rail reads as pinned
	// rather than floating over the tiles. The bar watches ITSELF against a root
	// inset by one pixel more than the sticky offset: while it still sits in the
	// flow it is wholly inside that root (ratio 1), and the moment it pins at
	// `top: 48px` the inset clips it (ratio < 1). A zero-height sentinel can't
	// answer this — placed inside a sticky element it pins along with it and
	// therefore never leaves the viewport.
	useEffect(() => {
		const bar = barRef.current;
		if (!bar || typeof IntersectionObserver === 'undefined') return;
		const obs = new IntersectionObserver(
			([entry]) => {
				bar.dataset.scrolled = entry?.isIntersecting ? 'false' : 'true';
			},
			{ threshold: [1], rootMargin: `-${STICKY_TOP + 1}px 0px 0px 0px` },
		);
		obs.observe(bar);
		return () => obs.disconnect();
	}, []);

	/** Move selection with the arrow keys — selection follows focus. */
	function handleTablistKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
		if (ordered.length === 0) return;
		let nextIndex: number | null = null;
		const currentIndex = Math.max(
			0,
			ordered.findIndex((a) => a.id === selectedId),
		);
		if (e.key === 'ArrowRight') nextIndex = (currentIndex + 1) % ordered.length;
		else if (e.key === 'ArrowLeft')
			nextIndex = (currentIndex - 1 + ordered.length) % ordered.length;
		else if (e.key === 'Home') nextIndex = 0;
		else if (e.key === 'End') nextIndex = ordered.length - 1;
		if (nextIndex == null) return;
		e.preventDefault();
		const next = ordered[nextIndex];
		if (!next) return;
		onSelect(next.id);
		tabRefs.current.get(next.id)?.focus();
	}

	function renderTab(agent: AgentEntity) {
		const isSelected = agent.id === selectedId;
		const isServing = agent.status === 'active';
		const isPending = agent.status === 'pending';
		const gaps = setupGaps.get(agent.id) ?? 0;
		const apiCount = apiCounts.get(agent.id);
		return (
			<button
				key={agent.id}
				ref={(el) => {
					if (el) tabRefs.current.set(agent.id, el);
					else tabRefs.current.delete(agent.id);
				}}
				type="button"
				role="tab"
				aria-selected={isSelected}
				tabIndex={isSelected ? 0 : -1}
				onClick={() => onSelect(agent.id)}
				className={cn(
					'flex shrink-0 snap-start items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors duration-150',
					'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
					isSelected
						? 'bg-background text-foreground font-medium shadow-sm'
						: 'text-muted-foreground hover:text-foreground',
				)}
			>
				{/* Active is the quiet default: only a non-active agent carries a
				    dot, so the dot itself means "this one is not serving". */}
				{!isServing && (
					<span
						aria-hidden="true"
						className={cn(
							'h-1.5 w-1.5 shrink-0 rounded-full',
							STATUS_DOT[agent.status],
						)}
					/>
				)}
				<span>{agent.name}</span>
				{isPending && <span className="sr-only">(awaiting approval)</span>}
				{apiCount != null && (
					<span
						className={cn(
							'text-xs tabular-nums',
							isSelected ? 'text-muted-foreground' : 'text-muted-foreground/70',
						)}
					>
						{apiCount}
					</span>
				)}
				{gaps > 0 && <span className="text-warning text-xs">· {gaps} to set up</span>}
			</button>
		);
	}

	const mask = edgeMask(edges.start, edges.end);

	return (
		<div
			ref={barRef}
			data-scrolled="false"
			data-testid="agent-strip"
			// Bleeds to the gutter edges so the blurred backdrop covers the tiles
			// passing underneath. The border is transparent until it sticks, so
			// pinning costs no layout shift.
			className="-mx-page-gutter px-page-gutter bg-background/85 data-[scrolled=true]:border-border/40 sticky top-12 z-20 border-b border-transparent py-2 backdrop-blur transition-[box-shadow,border-color] data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
		>
			{/* The rail's own surface sits OUTSIDE the scroller, so the fade
			    thins the tabs at an end without thinning the rail itself. */}
			<div className="bg-muted/40 relative rounded-lg">
				<div
					ref={scrollerRef}
					role="tablist"
					aria-label="Agents"
					onKeyDown={handleTablistKeyDown}
					onScroll={syncEdges}
					style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
					className={cn(
						'flex min-w-0 snap-x items-center gap-0.5 overflow-x-auto px-1.5 py-1',
						// One row at every width — the rail scrolls, it never wraps.
						'[scrollbar-width:none] flex-nowrap [&::-webkit-scrollbar]:hidden',
					)}
				>
					{visible.pending.length > 0 && (
						<>
							{/* Group opener — visual only (the sr-only tab suffix
							    carries the state), so the tablist keeps tab-only
							    semantics for AT. */}
							<span
								aria-hidden="true"
								data-testid="strip-pending-label"
								className="text-warning flex shrink-0 items-center gap-1.5 px-1.5 text-xs font-medium"
							>
								<span className="bg-warning h-1.5 w-1.5 animate-pulse rounded-full motion-reduce:animate-none" />
								Waiting
							</span>
							{visible.pending.map(renderTab)}
							{visible.rest.length > 0 && (
								<span
									aria-hidden="true"
									data-testid="strip-pending-divider"
									className="bg-border mx-1.5 h-5 w-px shrink-0 self-center"
								/>
							)}
						</>
					)}
					{visible.rest.map(renderTab)}
					{ordered.length === 0 && (
						<p className="text-muted-foreground px-1.5 py-1 text-sm">
							No agents match your filter.
						</p>
					)}
				</div>
			</div>
		</div>
	);
}
