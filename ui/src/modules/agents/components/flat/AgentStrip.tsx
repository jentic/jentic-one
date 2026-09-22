/**
 * AgentStrip — the agent selector on the flat Agents surface: text tabs carrying a
 * muted API count, a lifecycle glyph, and a "· N to set up" hint while bound
 * credentials await sign-in.
 *
 * Pending agents form a visual group at the head of the rail, but every tab stays a
 * direct child of the one `role="tablist"`, which has a roving tabindex and
 * selection following focus.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { STATUS_ICON, STATUS_TINT } from '@/shared/ui';
import type { ActorStatus } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { AgentEntity } from '@/modules/agents/api';

/** Width of the scroll-affordance fade at either end of the rail. */
const FADE = '1.5rem';

/** Where the rail pins: under the fixed `h-12` TopNavbar. In sync with the
 * `sticky top-12` class below, which the pinned-state observer reads. */
const STICKY_TOP = 48;

/** Non-active states whose verdict is in: their tabs read struck through.
 * `pending` is still in motion, so crossing it out would state the opposite. */
const SETTLED_STATUSES: ReadonlySet<ActorStatus> = new Set<ActorStatus>([
	'rejected',
	'disabled',
	'archived',
]);

/** How far the rail quiets each status glyph. Hues come from `STATUS_TINT`. */
const TAB_STATUS_QUIETING: Record<ActorStatus, string> = {
	// Full strength: the one state asking for a decision.
	pending: 'opacity-100',
	// Quietest of the five: it is on most tabs most of the time.
	active: 'opacity-70',
	rejected: 'opacity-80',
	disabled: 'opacity-80',
	archived: 'opacity-60',
};

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
	/** Per-agent count of reachable APIs. A missing key shows no count rather
	 * than a figure the join can't prove. */
	apiCounts: ReadonlyMap<string, number>;
	/** The page header's filter text (this component consumes it, not owns it). */
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
		// Pending tabs lead, re-asserted here so DOM order and arrow-key traversal
		// cannot disagree with the visual group.
		return {
			pending: matched.filter((a) => a.status === 'pending'),
			rest: matched.filter((a) => a.status !== 'pending'),
		};
	}, [agents, filter]);
	const ordered = useMemo(() => [...visible.pending, ...visible.rest], [visible]);

	/**
	 * Where the rail's keyboard focus sits: the selected tab while the filter still
	 * shows it, else the first visible tab. A roving tabindex makes exactly one tab
	 * focusable, so "the selected tab" alone would leave a filtered-out selection
	 * with every tab at `-1` — a rail no keyboard can enter.
	 */
	const focusIndex = useMemo(() => {
		const found = ordered.findIndex((a) => a.id === selectedId);
		return found >= 0 ? found : 0;
	}, [ordered, selectedId]);
	const focusableId = ordered[focusIndex]?.id ?? null;

	const syncEdges = useCallback(() => {
		const scroller = scrollerRef.current;
		if (!scroller) return;
		// 1px of slack: fractional layout widths make an unscrollable rail
		// report a sub-pixel overflow, which would fade an end for no reason.
		const max = scroller.scrollWidth - scroller.clientWidth;
		const next = { start: scroller.scrollLeft > 1, end: scroller.scrollLeft < max - 1 };
		setEdges((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
	}, []);

	// Overflow changes on scroll, on resize and as the tab count changes. Counts
	// and gap hints land later and WIDEN tabs, which the observer cannot see.
	useEffect(() => {
		syncEdges();
		const scroller = scrollerRef.current;
		if (!scroller || typeof ResizeObserver === 'undefined') return;
		const obs = new ResizeObserver(syncEdges);
		obs.observe(scroller);
		return () => obs.disconnect();
	}, [syncEdges, ordered.length, setupGaps, apiCounts]);

	// The selection marker — wash and underline — is ONE element owned by the rail,
	// so it slides from tab to tab. Re-measured when anything can move or widen one.
	const [marker, setMarker] = useState<{
		left: number;
		top: number;
		width: number;
		height: number;
	} | null>(null);
	useLayoutEffect(() => {
		const tab = selectedId ? tabRefs.current.get(selectedId) : null;
		if (!tab) {
			setMarker(null);
			return;
		}
		setMarker({
			left: tab.offsetLeft,
			top: tab.offsetTop,
			width: tab.offsetWidth,
			height: tab.offsetHeight,
		});
	}, [selectedId, ordered, setupGaps, apiCounts]);

	// Selection also arrives without focus (the banner's Review, an `?agent=` deep
	// link). Scrolls the rail only, never an ancestor.
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

	// Hairline once the rail is actually pinned: the bar watches ITSELF against a
	// root inset one pixel past the sticky offset, which clips it only when pinned.
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
		if (e.key === 'ArrowRight') nextIndex = (focusIndex + 1) % ordered.length;
		else if (e.key === 'ArrowLeft')
			nextIndex = (focusIndex - 1 + ordered.length) % ordered.length;
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
		const isPending = agent.status === 'pending';
		const isSettled = SETTLED_STATUSES.has(agent.status);
		const StatusIcon = STATUS_ICON[agent.status];
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
				tabIndex={agent.id === focusableId ? 0 : -1}
				onClick={() => onSelect(agent.id)}
				className={cn(
					// `relative` lifts the tab above the sliding marker, which paints behind it.
					'relative flex shrink-0 snap-start items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors duration-150',
					'focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:outline-none',
					// No background of its own — the sliding marker is the wash and underline.
					isSelected
						? 'text-foreground font-semibold'
						: // Dimmed so the marker registers — `--muted-foreground` is near-white here.
							'text-muted-foreground/65 hover:text-foreground hover:bg-muted/50',
				)}
			>
				{/* Every state gets a glyph, active included: the shape tells the five apart. */}
				<StatusIcon
					aria-hidden="true"
					className={cn(
						'size-3.5 shrink-0',
						STATUS_TINT[agent.status],
						TAB_STATUS_QUIETING[agent.status],
					)}
				/>
				{/* Struck through when the name will not serve and won't change on its own. */}
				<span className={cn(isSettled && 'line-through decoration-from-font')}>
					{agent.name}
				</span>
				{isPending && <span className="sr-only">(awaiting approval)</span>}
				{apiCount != null && (
					<span
						className={cn(
							'text-xs tabular-nums',
							isSelected ? 'text-muted-foreground' : 'text-muted-foreground/50',
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
			// Bleeds to the gutter edges so the backdrop covers the tiles passing under.
			// The border is transparent until it sticks, so pinning costs no layout shift.
			className="-mx-page-gutter px-page-gutter bg-background/85 data-[scrolled=true]:border-border/40 sticky top-12 z-20 border-b border-transparent py-2 backdrop-blur transition-[box-shadow,border-color] data-[scrolled=true]:shadow-[0_1px_0_0_rgb(0_0_0_/0.04)]"
		>
			{/* The rail's own surface sits OUTSIDE the scroller, so the fade
			    thins the tabs at an end without thinning the rail itself. */}
			<div className="bg-muted/40 relative rounded-lg">
				{ordered.length > 0 && (
					<div
						ref={scrollerRef}
						role="tablist"
						aria-label="Agents"
						onKeyDown={handleTablistKeyDown}
						onScroll={syncEdges}
						style={mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined}
						className={cn(
							// `relative` anchors the sliding marker inside the scroll content.
							'relative flex min-w-0 snap-x items-center gap-0.5 overflow-x-auto px-1.5 py-1',
							// One row at every width — the rail scrolls, it never wraps.
							'[scrollbar-width:none] flex-nowrap [&::-webkit-scrollbar]:hidden',
						)}
					>
						{/* The selection marker, sliding as one piece. First child and positioned, so
						    it paints behind the tabs' text; the underline starts at the NAME's left edge. */}
						{marker && (
							<span
								aria-hidden="true"
								data-testid="strip-selection-marker"
								className="bg-primary/15 ring-primary/30 pointer-events-none absolute rounded-md ring-1 transition-[left,top,width,height] duration-200 ease-out motion-reduce:transition-none"
								style={{
									left: marker.left,
									top: marker.top,
									width: marker.width,
									height: marker.height,
								}}
							>
								<span className="bg-primary absolute right-2.5 bottom-0.5 left-[30px] h-0.5 rounded-full" />
							</span>
						)}
						{visible.pending.length > 0 && (
							<>
								{/* Visual only — the sr-only tab suffix carries the state. */}
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
					</div>
				)}
				{/* Replaces the rail rather than sitting inside it: a tablist owns tabs and
				    nothing else. `status` announces it when a keystroke empties the rail. */}
				{ordered.length === 0 && (
					<p role="status" className="text-muted-foreground px-3 py-2.5 text-sm">
						No agents match your filter.
					</p>
				)}
			</div>
		</div>
	);
}
