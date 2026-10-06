/**
 * The New agent panel's register-tab state: when this opening began, the agent
 * it is finishing, the typed name, and the roster poll that watches for the
 * arrival. The rules are pure, in `registerPanel.ts`.
 *
 * Detection is the landing's: the live agent stream invalidates the roster on
 * every `agent.*` event, and while the stream is down or lagging the roster is
 * polled every few seconds as the fallback — only while the panel is open on
 * its register tab and has no approved agent yet. Every opening starts fresh:
 * a new opening time and roster snapshot, nothing tracked, and a fresh name suggestion.
 */
import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { pendingAgentsCountKey } from '@/shared/hooks';
import { useAgentStreamOptional } from '@/shared/lib';
import {
	useAgents,
	type AgentEntity,
	type useApproveAgent,
	type useDenyAgent,
} from '@/modules/agents/api';
import { FIRST_AGENT_POLL_MS } from '@/modules/agents/lib/useFirstAgentLanding';
import { useRegisterName } from '@/modules/agents/lib/useRegisterName';
import { derivePanelArrival } from '@/modules/agents/lib/registerPanel';

const NO_AGENTS: AgentEntity[] = [];

export interface RegisterPanelOptions {
	/** Whether the panel is open. */
	open: boolean;
	/** Whether its register tab is the one on screen. */
	active: boolean;
	approve: Pick<ReturnType<typeof useApproveAgent>, 'variables' | 'isPending' | 'isSuccess'>;
	deny: Pick<ReturnType<typeof useDenyAgent>, 'variables' | 'isSuccess'>;
}

export function useRegisterPanel({ open, active, approve, deny }: RegisterPanelOptions) {
	const [openedAt, setOpenedAt] = useState(() => Date.now());
	const [knownAtOpen, setKnownAtOpen] = useState<ReadonlySet<string>>(() => new Set());
	const [trackedId, setTrackedId] = useState<string | null>(null);
	// The phase the poll was last gated on (the roster is read below it).
	const [approved, setApproved] = useState(false);
	const stream = useAgentStreamOptional();
	const polling = open && active && !approved && stream?.status !== 'live';
	// Off while closed: the fleet's own observer owns the roster (and its
	// drain) then, and this one must not add refetches to it.
	const query = useAgents({
		status: 'all',
		enabled: open,
		refetchInterval: polling ? FIRST_AGENT_POLL_MS : false,
	});
	const agents = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? NO_AGENTS,
		[query.data],
	);
	const names = useMemo(() => agents.map((a) => a.name), [agents]);
	const rosterRead = !query.isPending && (!query.hasNextPage || query.isError);
	// Whether the command was on screen as of the last render: the phase is
	// derived from the command's name below, so the name reads the phase back.
	const [listening, setListening] = useState(true);
	// "my-agent" over a fleet, "my-first-agent" in an org with no agent at all.
	const nameDraft = useRegisterName({ names, rosterRead, listening });

	// Reset during render on the opening itself, so the first frame of a new
	// opening never shows the last one's agent.
	const [wasOpen, setWasOpen] = useState(open);
	if (open !== wasOpen) {
		setWasOpen(open);
		if (open) {
			setOpenedAt(Date.now());
			setKnownAtOpen(new Set(agents.map((a) => a.id)));
			setTrackedId(null);
			nameDraft.reset();
		}
	}

	const { commandName } = nameDraft;
	const deniedId = deny.isSuccess ? (deny.variables?.id ?? null) : null;
	const view = derivePanelArrival({
		agents,
		openedAt,
		knownAtOpen,
		trackedId: open ? trackedId : null,
		deniedId,
		expectedName: commandName,
	});
	const agent = open ? view.agent : null;
	const phase = open ? view.phase : 'listening';
	if ((phase === 'approved') !== approved) setApproved(phase === 'approved');
	if ((phase === 'listening') !== listening) setListening(phase === 'listening');

	const agentId = agent?.id ?? null;
	useEffect(() => {
		if (agentId != null) setTrackedId(agentId);
	}, [agentId]);
	// A pending arrival also feeds the nav badge and the fleet's approval
	// banner, a separate query the fallback poll does not refresh (a live stream
	// refreshes it on its own).
	const queryClient = useQueryClient();
	useEffect(() => {
		if (agentId != null && polling)
			void queryClient.invalidateQueries({ queryKey: pendingAgentsCountKey });
	}, [agentId, polling, queryClient]);

	// Approve reads as in flight until the roster itself says active.
	const approving =
		agent != null &&
		approve.variables === agent.id &&
		(approve.isPending || (approve.isSuccess && agent.status === 'pending'));

	return {
		agent,
		phase,
		morePending: open ? view.morePending : 0,
		approving,
		registerName: nameDraft.name,
		setRegisterName: nameDraft.setName,
		/** The name the displayed command registers with. */
		commandName,
		/** The existing agent name the typed one duplicates, or `null`. */
		registerNameDuplicateOf: nameDraft.duplicateOf,
		/** Every agent name in the org, for the create form's duplicate hint. */
		names,
		/** Whether the roster poll is running (the stream's fallback). */
		polling,
	};
}
