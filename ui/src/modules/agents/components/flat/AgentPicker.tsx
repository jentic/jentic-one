/**
 * AgentPicker — the searchable "Switch agent" palette over the whole fleet,
 * opened by ⌘K / Ctrl+K anywhere on the Agents surface and by the strip's `+N`
 * and "All agents" buttons.
 *
 * A modal `Dialog` holding a combobox: the filter field owns focus and points
 * at the highlighted row through `aria-activedescendant`; the rows are grouped
 * "Waiting for approval" then "Agents". Arrows move, Enter switches, Esc clears
 * the filter first and then closes (the dialog hands focus back to its opener).
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Check, Filter } from 'lucide-react';
import {
	AgentBadge,
	Dialog,
	Kbd,
	SearchInput,
	STATUS_ICON,
	STATUS_LABELS,
	STATUS_TINT,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { AgentEntity } from '@/modules/agents/api';
import { matchesAgent, plural } from '@/modules/agents/lib/stripSummary';

interface AgentPickerProps {
	open: boolean;
	onClose: () => void;
	/** The whole fleet, in strip order (waiting first). */
	agents: readonly AgentEntity[];
	selectedId: string | null;
	onSelect: (id: string) => void;
	apiCounts: ReadonlyMap<string, number>;
	/** More of the fleet exists than `agents` holds (pages still to come, or a
	 * later one failed): the count reads as a floor. */
	incomplete?: boolean;
}

export function AgentPicker({
	open,
	onClose,
	agents,
	selectedId,
	onSelect,
	apiCounts,
	incomplete = false,
}: AgentPickerProps) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Switch agent"
			subtitle={
				incomplete
					? `At least ${plural(agents.length, 'agent')}`
					: plural(agents.length, 'agent')
			}
			size="md"
			className="sm:max-w-[30rem]"
		>
			{/* Mounted only while open, so its field never doubles a label on the page. */}
			{open && (
				<PickerBody
					agents={agents}
					selectedId={selectedId}
					apiCounts={apiCounts}
					onDismiss={onClose}
					onChoose={(id) => {
						if (id !== selectedId) onSelect(id);
						onClose();
					}}
				/>
			)}
		</Dialog>
	);
}

function PickerBody({
	agents,
	selectedId,
	apiCounts,
	onChoose,
	onDismiss,
}: {
	agents: readonly AgentEntity[];
	selectedId: string | null;
	apiCounts: ReadonlyMap<string, number>;
	onChoose: (id: string) => void;
	onDismiss: () => void;
}) {
	const baseId = useId();
	const listId = `${baseId}-list`;
	const optionId = (id: string) => `${baseId}-opt-${id}`;
	const [query, setQuery] = useState('');
	const fieldRef = useRef<HTMLInputElement>(null);
	// The dialog's `showModal()` runs after this mounts and focuses its first
	// control (Close); the field takes focus back a frame later, so typing
	// after ⌘K lands in it.
	useEffect(() => {
		const raf = requestAnimationFrame(() => fieldRef.current?.focus());
		return () => cancelAnimationFrame(raf);
	}, []);

	const groups = useMemo(() => {
		const matched = agents.filter((a) => matchesAgent(a, query));
		return [
			{
				key: 'waiting',
				label: 'Waiting for approval',
				rows: matched.filter((a) => a.status === 'pending'),
			},
			{ key: 'agents', label: 'Agents', rows: matched.filter((a) => a.status !== 'pending') },
		].filter((g) => g.rows.length > 0);
	}, [agents, query]);
	const flat = useMemo(() => groups.flatMap((g) => g.rows), [groups]);

	// The highlight starts on the current agent, then on the first match.
	const [activeId, setActiveId] = useState<string | null>(selectedId);
	const active = flat.find((a) => a.id === activeId) ?? flat[0] ?? null;

	const activeDomId = active ? optionId(active.id) : null;
	useEffect(() => {
		if (activeDomId) document.getElementById(activeDomId)?.scrollIntoView({ block: 'nearest' });
	}, [activeDomId]);

	function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
		// Esc clears a typed filter first, then closes (the native `cancel` is
		// held off, so one press never does both).
		if (e.key === 'Escape') {
			e.preventDefault();
			if (query) {
				setQuery('');
				setActiveId(null);
			} else onDismiss();
			return;
		}
		if (flat.length === 0) return;
		const at = active ? flat.indexOf(active) : -1;
		let next: number | null = null;
		if (e.key === 'ArrowDown') next = (at + 1) % flat.length;
		else if (e.key === 'ArrowUp') next = (at - 1 + flat.length) % flat.length;
		else if (e.key === 'Enter') {
			e.preventDefault();
			if (active) onChoose(active.id);
			return;
		}
		if (next == null) return;
		e.preventDefault();
		setActiveId(flat[next].id);
	}

	return (
		<div className="-mx-1 space-y-3">
			<SearchInput
				value={query}
				onValueChange={(v) => {
					setQuery(v);
					setActiveId(null);
				}}
				ref={fieldRef}
				icon={<Filter className="h-3.5 w-3.5" />}
				placeholder="Find an agent by name…"
				aria-label="Find an agent"
				role="combobox"
				aria-expanded="true"
				aria-controls={listId}
				aria-autocomplete="list"
				aria-activedescendant={active ? optionId(active.id) : undefined}
				onKeyDown={onKeyDown}
			/>
			<div
				id={listId}
				role="listbox"
				aria-label="Agents"
				className="max-h-[min(26rem,55dvh)] overflow-y-auto overscroll-contain"
			>
				{groups.map((g) => (
					<div
						key={g.key}
						role="group"
						aria-labelledby={`${baseId}-${g.key}`}
						className="pb-1.5"
					>
						<div
							id={`${baseId}-${g.key}`}
							className="text-foreground-faint px-2.5 pt-1.5 pb-1 text-[11px] font-semibold tracking-[0.04em] uppercase"
						>
							{g.label}
						</div>
						{g.rows.map((a) => (
							<PickerRow
								key={a.id}
								id={optionId(a.id)}
								agent={a}
								apiCount={apiCounts.get(a.id)}
								active={a.id === active?.id}
								current={a.id === selectedId}
								onHover={() => setActiveId(a.id)}
								onChoose={() => onChoose(a.id)}
							/>
						))}
					</div>
				))}
			</div>
			{flat.length === 0 && (
				<p role="status" className="text-foreground-sub px-2.5 py-3 text-sm">
					No agents match “{query.trim()}”.
				</p>
			)}
			<p
				aria-hidden="true"
				className="text-foreground-faint flex items-center gap-3 px-2.5 text-[11px]"
			>
				<span className="inline-flex items-center gap-1">
					<Kbd>↑</Kbd>
					<Kbd>↓</Kbd> move
				</span>
				<span className="inline-flex items-center gap-1">
					<Kbd>Enter</Kbd> switch
				</span>
				<span className="inline-flex items-center gap-1">
					<Kbd>Esc</Kbd> close
				</span>
			</p>
		</div>
	);
}

function PickerRow({
	id,
	agent,
	apiCount,
	active,
	current,
	onHover,
	onChoose,
}: {
	id: string;
	agent: AgentEntity;
	apiCount: number | undefined;
	active: boolean;
	current: boolean;
	onHover: () => void;
	onChoose: () => void;
}) {
	const StatusIcon = STATUS_ICON[agent.status];
	const meta =
		agent.status === 'active' && apiCount != null
			? plural(apiCount, 'API')
			: STATUS_LABELS[agent.status];
	return (
		// The field keeps focus; the row is reached through `aria-activedescendant`
		// and chosen with Enter there, so it needs no key handler of its own.
		<div
			id={id}
			role="option"
			aria-selected={active}
			data-agent-id={agent.id}
			data-current={current || undefined}
			onMouseMove={onHover}
			onClick={onChoose}
			className={cn(
				'flex h-9 cursor-pointer items-center gap-2.5 rounded-md px-2.5 text-[13.5px] font-medium',
				active ? 'bg-surface-chip text-foreground-name' : 'text-foreground-sub',
			)}
		>
			<AgentBadge id={agent.id} name={agent.name} size="xs" shape="circle" />
			<span
				dir="auto"
				className={cn(
					'min-w-0 flex-1 truncate',
					(agent.status === 'rejected' || agent.status === 'archived') &&
						'line-through decoration-from-font',
				)}
			>
				{agent.name}
			</span>
			<span className="text-foreground-faint inline-flex shrink-0 items-center gap-1.5 text-xs tabular-nums">
				<StatusIcon
					aria-hidden="true"
					className={cn('size-3.5', STATUS_TINT[agent.status])}
				/>
				{meta}
			</span>
			<span className="inline-flex w-4 shrink-0 justify-center">
				{current && (
					<>
						<Check aria-hidden="true" className="text-primary size-3.5" />
						<span className="sr-only">(current)</span>
					</>
				)}
			</span>
		</div>
	);
}
