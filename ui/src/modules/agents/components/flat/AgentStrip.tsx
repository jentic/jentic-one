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
 * At narrow widths the rail scrolls horizontally (`overflow-x-auto`, snapped,
 * no mid-word truncation); from `sm` up it wraps instead.
 */
import { useMemo, useRef } from 'react';
import { STATUS_DOT } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { AgentEntity } from '@/modules/agents/api';

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

	return (
		<div
			role="tablist"
			aria-label="Agents"
			onKeyDown={handleTablistKeyDown}
			className={cn(
				'bg-muted/40 flex min-w-0 snap-x items-center gap-0.5 overflow-x-auto rounded-lg px-1.5 py-1',
				'sm:flex-wrap sm:overflow-visible',
				// Right-edge mask: a clipped tab fades instead of being cut, so
				// the rail reads as scrollable at narrow widths.
				'[mask-image:linear-gradient(to_right,black_calc(100%-1.5rem),transparent)] sm:[mask-image:none]',
			)}
		>
			{visible.pending.length > 0 && (
				<>
					{/* Group opener — visual only (the sr-only tab suffix carries
					    the state), so the tablist keeps tab-only semantics for AT. */}
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
	);
}
