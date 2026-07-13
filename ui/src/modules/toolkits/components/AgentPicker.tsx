/**
 * AgentPicker — searchable list of workspace agents for the toolkit
 * "Link agent" dialog.
 *
 * Mirrors CredentialPicker: list the agents the workspace already has
 * (name + id + status badge), filter by name/id, hide the agents already
 * linked to this toolkit, and link on click. Data comes from the toolkits
 * service tier (`useLinkableAgents`), which reads the org-wide `GET /agents`
 * surface through the shared API — no sibling Agents-module import
 * (module-boundary rule).
 */
import { useMemo, useState } from 'react';
import { motion, type Variants } from 'framer-motion';
import { Bot, ChevronRight, Filter, Link as LinkIcon, SearchX } from 'lucide-react';
import {
	ActorStatusBadge,
	AppLink,
	EmptyState,
	ErrorAlert,
	LoadingState,
	SearchInput,
} from '@/shared/ui';
import { useLinkableAgents } from '@/modules/toolkits/api';
import type { ToolkitAgent } from '@/modules/toolkits/api/types';
import { ROUTES } from '@/shared/app/routes';

interface AgentPickerProps {
	/** Agent ids already linked to this toolkit — hidden from the list. */
	linkedIds: Set<string>;
	/** Fired with the chosen agent id. */
	onSelect: (agentId: string) => void;
	/** Disables rows while a link mutation is in flight. */
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

export function AgentPicker({ linkedIds, onSelect, pending, enabled = true }: AgentPickerProps) {
	const [query, setQuery] = useState('');
	const { data, isLoading, error } = useLinkableAgents({ enabled });

	const available = useMemo(() => {
		const all = data ?? [];
		const q = query.trim().toLowerCase();
		return all.filter((a) => {
			if (linkedIds.has(a.agent_id)) return false;
			if (!q) return true;
			return a.agent_name.toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q);
		});
	}, [data, query, linkedIds]);

	const total = data?.length ?? 0;
	// Agents that could ever appear in the list (everything not already linked).
	// When this pool is empty there is nothing to filter, so the filter input is
	// disabled — typing could only ever stack a second empty state on top of the
	// "all linked" / "no agents" one.
	const candidateCount = useMemo(
		() => (data ?? []).filter((a) => !linkedIds.has(a.agent_id)).length,
		[data, linkedIds],
	);
	const allLinked = total > 0 && candidateCount === 0;

	return (
		<div className="space-y-3">
			<SearchInput
				value={query}
				onValueChange={setQuery}
				placeholder="Filter agents by name or id…"
				aria-label="Filter agents"
				icon={<Filter className="h-3.5 w-3.5" />}
				disabled={candidateCount === 0}
				autoFocus
			/>

			{error && <ErrorAlert message={(error as Error).message} />}

			{isLoading && <LoadingState message="Loading agents…" />}

			{!isLoading && !error && total === 0 && (
				<EmptyState
					icon={<Bot className="h-8 w-8" />}
					title="No agents yet"
					description="Register an agent first, then link it to this toolkit."
					action={
						<AppLink href={ROUTES.agents} className="text-primary font-medium">
							Go to Agents
						</AppLink>
					}
				/>
			)}

			{!isLoading && !error && allLinked && (
				<EmptyState
					icon={<LinkIcon className="h-8 w-8" />}
					title="All agents linked"
					description="Every agent in your workspace already has this toolkit."
				/>
			)}

			{!isLoading && !error && query.trim() && available.length === 0 && !allLinked && (
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
					{available.map((agent) => (
						<motion.li key={agent.agent_id} variants={ROW_VARIANTS}>
							<AgentRow agent={agent} onSelect={onSelect} disabled={pending} />
						</motion.li>
					))}
				</motion.ul>
			)}
		</div>
	);
}

function AgentRow({
	agent,
	onSelect,
	disabled,
}: {
	agent: ToolkitAgent;
	onSelect: (agentId: string) => void;
	disabled?: boolean;
}) {
	return (
		<button
			type="button"
			disabled={disabled}
			onClick={() => onSelect(agent.agent_id)}
			data-testid="agent-picker-row"
			className="group hover:border-primary/50 bg-background hover:bg-muted/40 border-border flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
		>
			<div className="bg-primary/10 text-primary flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
				<Bot className="h-4 w-4" />
			</div>
			<div className="min-w-0 flex-1">
				<span className="text-foreground block truncate text-sm font-medium">
					{agent.agent_name}
				</span>
				<p className="text-muted-foreground mt-0.5 truncate font-mono text-xs">
					{agent.agent_id}
				</p>
			</div>
			<ActorStatusBadge status={agent.status} className="shrink-0 text-[10px]" />
			<ChevronRight className="text-muted-foreground group-hover:text-foreground h-4 w-4 shrink-0 transition-colors" />
		</button>
	);
}
