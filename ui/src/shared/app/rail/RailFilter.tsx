/**
 * RailFilter — the Activity rail's one Filter control: a trigger that reads
 * back what you're looking at ("Everyone", "Support Triage · Calls") and a
 * popover with the two questions the feed answers:
 *
 *   • Who   — everyone, or one agent / person (searchable, with how many
 *             loaded events each has). On an agent's page that agent is
 *             pinned on top as the one-click "Only this agent" shortcut.
 *   • What  — Calls / APIs / Credentials / Agents; none picked = everything.
 *
 * Failures only stays a separate toggle beside it: it carries the failure
 * count, and it's the filter you flip most.
 *
 * Stateless about the choices themselves (they live in the stream provider);
 * this only owns whether the popover is open and the search text.
 */
import { useId, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Check, ChevronDown, ListFilter } from 'lucide-react';
import { SearchInput, useDismissable } from '@/shared/ui';
import { ACTIVITY_CATEGORIES } from '@/shared/lib/agentStream';
import type { ActivityCategory } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

export type ActorOption = {
	/** `"<actorType>:<actorId>"`; `''` is everyone. */
	value: string;
	label: string;
	section: 'agents' | 'people' | 'other';
	/** Loaded events under this lens. */
	count: number;
};

const SECTION_LABEL: Record<ActorOption['section'], string> = {
	agents: 'Agents',
	people: 'People',
	other: 'Other',
};

/** Only offer search once the list is long enough to need it. */
const SEARCH_THRESHOLD = 6;

export type RailFilterProps = {
	/** Selected actor value (`''` = everyone). */
	value: string;
	options: ActorOption[];
	/** Loaded events in total — the "Everyone" count. */
	totalCount: number;
	onChange: (value: string) => void;
	categories: ReadonlySet<ActivityCategory>;
	onCategoriesChange: (next: ReadonlySet<ActivityCategory>) => void;
	/** The agent the current page is about, when it isn't already the lens. */
	suggestion?: { value: string; label: string } | null;
};

/** The trigger's read-back: who (may truncate) and what (a chip that doesn't). */
function summarize(
	value: string,
	options: ActorOption[],
	categories: ReadonlySet<ActivityCategory>,
): { who: string; what: string | null } {
	const who = value ? (options.find((o) => o.value === value)?.label ?? value) : 'Everyone';
	if (categories.size === 0) return { who, what: null };
	const labels = ACTIVITY_CATEGORIES.filter((c) => categories.has(c.value)).map((c) => c.label);
	return { who, what: labels.length === 1 ? labels[0] : `${labels.length} kinds` };
}

export function RailFilter({
	value,
	options,
	totalCount,
	onChange,
	categories,
	onCategoriesChange,
	suggestion,
}: RailFilterProps) {
	const [open, setOpen] = useState(false);
	const [query, setQuery] = useState('');
	const reduce = useReducedMotion();
	const triggerRef = useRef<HTMLButtonElement | null>(null);
	const panelId = useId();

	function close(focusTrigger = true) {
		setOpen(false);
		setQuery('');
		if (focusTrigger) triggerRef.current?.focus();
	}
	const ref = useDismissable<HTMLDivElement>(open, () => close(false));

	const filtered = value !== '' || categories.size > 0;
	const summary = summarize(value, options, categories);

	const sections = useMemo(() => {
		const q = query.trim().toLowerCase();
		const matching = q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
		return (['agents', 'people', 'other'] as const)
			.map((section) => ({
				section,
				items: matching.filter((o) => o.section === section),
			}))
			.filter((s) => s.items.length > 0);
	}, [options, query]);

	function pick(next: string) {
		onChange(next);
		close();
	}

	function toggleCategory(c: ActivityCategory) {
		const next = new Set(categories);
		if (next.has(c)) next.delete(c);
		else next.add(c);
		onCategoriesChange(next);
	}

	return (
		<div
			ref={ref}
			className="relative min-w-0 flex-1"
			// Escape from inside the popover (e.g. the search box) hands focus back
			// to the trigger; an outside click leaves it where the user clicked.
			// It closes only the popover — not the drawer sheet around it.
			onKeyDown={(e) => {
				if (e.key !== 'Escape' || !open) return;
				e.stopPropagation();
				close();
			}}
		>
			<button
				ref={triggerRef}
				type="button"
				onClick={() => (open ? close() : setOpen(true))}
				aria-haspopup="dialog"
				aria-expanded={open}
				aria-controls={open ? panelId : undefined}
				title={summary.what ? `${summary.who} · ${summary.what}` : summary.who}
				className={cn(
					'border-border bg-background/60 hover:bg-background flex h-8 w-full min-w-0 items-center gap-1.5 rounded-lg border px-2 text-xs transition-colors',
					open && 'border-primary/50',
				)}
			>
				<ListFilter
					className={cn(
						'h-3.5 w-3.5 shrink-0',
						filtered ? 'text-primary' : 'text-muted-foreground',
					)}
					aria-hidden="true"
				/>
				<span className="sr-only">Show activity for: </span>
				<span className="text-foreground min-w-0 flex-1 truncate text-left">
					{summary.who}
				</span>
				{summary.what && (
					<span className="bg-primary/15 text-foreground shrink-0 rounded-full px-1.5 text-[10px] leading-4 font-medium">
						{summary.what}
					</span>
				)}
				<ChevronDown
					className={cn(
						'text-muted-foreground h-3.5 w-3.5 shrink-0 transition-transform duration-200',
						open && 'rotate-180',
					)}
					aria-hidden="true"
				/>
			</button>

			<AnimatePresence>
				{open && (
					<motion.div
						id={panelId}
						role="dialog"
						aria-label="Filter activity"
						initial={reduce ? false : { opacity: 0, y: -4, scale: 0.98 }}
						animate={{ opacity: 1, y: 0, scale: 1 }}
						exit={reduce ? undefined : { opacity: 0, y: -4, scale: 0.98 }}
						transition={{ duration: 0.16, ease: [0.32, 0.72, 0, 1] }}
						style={{ transformOrigin: 'top left' }}
						className="border-border bg-background absolute top-full left-0 z-50 mt-1.5 w-[calc(100%+6.5rem)] max-w-[264px] rounded-lg border p-1.5 shadow-lg"
					>
						{suggestion && (
							<div className="bg-primary/10 mb-1.5 flex items-center gap-2 rounded-md px-2 py-1.5">
								<span className="flex min-w-0 flex-col leading-tight">
									<span className="text-muted-foreground text-[10px]">
										On this page
									</span>
									<span className="text-foreground truncate text-xs font-medium">
										{suggestion.label}
									</span>
								</span>
								<button
									type="button"
									onClick={() => pick(suggestion.value)}
									className="bg-primary text-primary-foreground ml-auto h-6 shrink-0 rounded-md px-2 text-[11px] font-semibold"
								>
									Only this agent
								</button>
							</div>
						)}

						<p className="text-muted-foreground px-1.5 pt-0.5 pb-1 text-[10px] font-semibold tracking-wider uppercase">
							Who
						</p>
						{options.length >= SEARCH_THRESHOLD && (
							<SearchInput
								size="sm"
								value={query}
								onValueChange={setQuery}
								placeholder="Find an agent or person"
								aria-label="Find an agent or person"
								autoFocus
								className="mb-1"
							/>
						)}
						<div
							role="group"
							aria-label="Who"
							className="max-h-56 space-y-px overflow-y-auto [mask-image:linear-gradient(to_bottom,black_calc(100%-16px),transparent)] pb-3"
						>
							{!query && (
								<ActorButton
									label="Everyone"
									count={totalCount}
									selected={value === ''}
									onClick={() => pick('')}
								/>
							)}
							{sections.map(({ section, items }) => (
								<div key={section}>
									<p className="text-muted-foreground/80 px-2 pt-1.5 pb-0.5 text-[10px] font-medium">
										{SECTION_LABEL[section]}
									</p>
									{items.map((o) => (
										<ActorButton
											key={o.value}
											label={o.label}
											count={o.count}
											selected={value === o.value}
											onClick={() => pick(o.value)}
										/>
									))}
								</div>
							))}
							{query && sections.length === 0 && (
								<p className="text-muted-foreground px-2 py-3 text-center text-[11px]">
									No one matches “{query}”.
								</p>
							)}
						</div>

						<div className="border-border mt-1.5 border-t pt-1.5">
							<p className="text-muted-foreground px-1.5 pb-1 text-[10px] font-semibold tracking-wider uppercase">
								What
							</p>
							<div
								role="group"
								aria-label="What"
								className="flex flex-wrap gap-1 px-1"
							>
								{ACTIVITY_CATEGORIES.map((c) => {
									const on = categories.has(c.value);
									return (
										<button
											key={c.value}
											type="button"
											aria-pressed={on}
											onClick={() => toggleCategory(c.value)}
											className={cn(
												'h-6 rounded-full border px-2 text-[11px] font-medium transition-colors',
												on
													? 'border-primary/50 bg-primary/15 text-foreground'
													: 'border-border text-muted-foreground hover:text-foreground',
											)}
										>
											{c.label}
										</button>
									);
								})}
							</div>
						</div>

						{filtered && (
							<div className="mt-1.5 flex justify-end px-1">
								<button
									type="button"
									onClick={() => {
										onCategoriesChange(new Set());
										pick('');
									}}
									className="text-muted-foreground hover:text-foreground text-[11px] font-medium"
								>
									Reset filters
								</button>
							</div>
						)}
					</motion.div>
				)}
			</AnimatePresence>
		</div>
	);
}

function ActorButton({
	label,
	count,
	selected,
	onClick,
}: {
	label: string;
	count: number;
	selected: boolean;
	onClick: () => void;
}) {
	return (
		<button
			type="button"
			aria-pressed={selected}
			onClick={onClick}
			className={cn(
				'hover:bg-muted flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs transition-colors',
				selected ? 'text-foreground font-medium' : 'text-muted-foreground',
			)}
		>
			<Check
				className={cn('h-3 w-3 shrink-0', selected ? 'text-primary' : 'invisible')}
				aria-hidden="true"
			/>
			<span className="min-w-0 flex-1 truncate">{label}</span>
			<span
				className={cn(
					'text-[10px] tabular-nums',
					count === 0 ? 'text-muted-foreground/40' : 'text-muted-foreground/70',
				)}
			>
				{count}
			</span>
		</button>
	);
}
