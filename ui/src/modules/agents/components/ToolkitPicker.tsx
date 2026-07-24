/**
 * ToolkitPicker — searchable list of workspace toolkits for the agent-side
 * "Bind toolkit" dialog (#607).
 *
 * The agent-side mirror of the toolkits module's `AgentPicker`: list the
 * toolkits the workspace has (name + id), filter by name/id, hide the toolkits
 * already bound to this agent, and bind on click. Data comes from the agents
 * service tier (`useLinkableToolkits`), which reads the org-wide `GET /toolkits`
 * surface through the shared API — no sibling Toolkits-module import
 * (module-boundary rule).
 */
import { useMemo, useState } from 'react';
import { motion, type Variants } from 'framer-motion';
import { ChevronRight, Filter, Link as LinkIcon, SearchX, Shield } from 'lucide-react';
import { AppLink, Badge, EmptyState, ErrorAlert, LoadingState, SearchInput } from '@/shared/ui';
import { useLinkableToolkits } from '@/modules/agents/api';
import type { LinkableToolkit } from '@/modules/agents/api';
import { ROUTES } from '@/shared/app/routes';

interface ToolkitPickerProps {
	/** Toolkit ids already bound to this agent — hidden from the list. */
	boundIds: Set<string>;
	/** Fired with the chosen toolkit id. */
	onSelect: (toolkitId: string) => void;
	/** Disables rows while a bind mutation is in flight. */
	pending?: boolean;
	/** Only fetch when the host dialog is actually open. */
	enabled?: boolean;
}

const LIST_VARIANTS: Variants = {
	hidden: {},
	show: { transition: { staggerChildren: 0.03 } },
};

const ROW_VARIANTS: Variants = {
	hidden: { opacity: 0, y: 6 },
	show: { opacity: 1, y: 0, transition: { duration: 0.16, ease: 'easeOut' } },
};

export function ToolkitPicker({ boundIds, onSelect, pending, enabled = true }: ToolkitPickerProps) {
	const [query, setQuery] = useState('');
	const { data, isLoading, error } = useLinkableToolkits({ enabled });

	const available = useMemo(() => {
		const all = data ?? [];
		const q = query.trim().toLowerCase();
		return all.filter((t) => {
			if (boundIds.has(t.toolkitId)) return false;
			if (!q) return true;
			return t.name.toLowerCase().includes(q) || t.toolkitId.toLowerCase().includes(q);
		});
	}, [data, query, boundIds]);

	const total = data?.length ?? 0;
	// Toolkits that could ever appear (everything not already bound). When this
	// pool is empty there is nothing to filter, so the input is disabled — typing
	// could only stack a second empty state on the "all bound" / "none" one.
	const candidateCount = useMemo(
		() => (data ?? []).filter((t) => !boundIds.has(t.toolkitId)).length,
		[data, boundIds],
	);
	const allBound = total > 0 && candidateCount === 0;

	return (
		<div className="space-y-3">
			<SearchInput
				value={query}
				onValueChange={setQuery}
				placeholder="Filter toolkits by name or id…"
				aria-label="Filter toolkits"
				icon={<Filter className="h-3.5 w-3.5" />}
				disabled={candidateCount === 0}
				autoFocus
			/>

			{error && <ErrorAlert message={(error as Error).message} />}

			{isLoading && <LoadingState message="Loading toolkits…" />}

			{!isLoading && !error && total === 0 && (
				<EmptyState
					icon={<Shield className="h-8 w-8" />}
					title="No toolkits yet"
					description="Create a toolkit first, then bind it to this agent."
					action={
						<AppLink href={ROUTES.toolkits} className="text-primary font-medium">
							Go to Toolkits
						</AppLink>
					}
				/>
			)}

			{!isLoading && !error && allBound && (
				<EmptyState
					icon={<LinkIcon className="h-8 w-8" />}
					title="All toolkits bound"
					description="Every toolkit in your workspace is already bound to this agent."
				/>
			)}

			{!isLoading && !error && query.trim() && available.length === 0 && !allBound && (
				<EmptyState
					icon={<SearchX className="h-8 w-8" />}
					title="No matches"
					description={`Nothing matched "${query.trim()}".`}
				/>
			)}

			{available.length > 0 && (
				<motion.ul
					className="max-h-72 space-y-1.5 overflow-y-auto"
					variants={LIST_VARIANTS}
					initial="hidden"
					animate="show"
				>
					{available.map((toolkit) => (
						<motion.li key={toolkit.toolkitId} variants={ROW_VARIANTS}>
							<ToolkitRow toolkit={toolkit} onSelect={onSelect} disabled={pending} />
						</motion.li>
					))}
				</motion.ul>
			)}
		</div>
	);
}

function ToolkitRow({
	toolkit,
	onSelect,
	disabled,
}: {
	toolkit: LinkableToolkit;
	onSelect: (toolkitId: string) => void;
	disabled?: boolean;
}) {
	// A kill-switched toolkit is listed with a badge, but the row is not
	// clickable: binding one would create a relationship that immediately
	// ``403 toolkit_suspended``s on every call (rev-1 review #4). Keeping it
	// visible is more debuggable than silently hiding it — the operator can
	// see *why* it's not offered.
	const isSuspended = !toolkit.active;
	const isDisabled = disabled || isSuspended;
	const title = isSuspended
		? 'Suspended toolkits cannot be bound — restore the kill switch first.'
		: undefined;
	return (
		<button
			type="button"
			disabled={isDisabled}
			title={title}
			onClick={() => onSelect(toolkit.toolkitId)}
			data-testid="toolkit-picker-row"
			data-suspended={isSuspended || undefined}
			className="group hover:border-primary/50 bg-background hover:bg-muted/40 border-border flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
		>
			<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
				<Shield className="h-4 w-4" />
			</div>
			<div className="min-w-0 flex-1">
				<span className="text-foreground block truncate text-sm font-medium">
					{toolkit.name}
				</span>
				<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
					{toolkit.toolkitId}
				</p>
			</div>
			{isSuspended && (
				<Badge variant="danger" className="shrink-0 text-[10px]">
					suspended
				</Badge>
			)}
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</button>
	);
}
