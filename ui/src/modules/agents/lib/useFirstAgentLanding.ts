/**
 * The zero-agents landing's in-session state: whether it is up, the agent it is
 * finishing, the roster poll that watches for that agent, and the exits that
 * hand the page to the fleet view. The rules themselves are pure, in
 * `firstRun.ts`.
 *
 * The roster is read here because the poll depends on the landing: while it
 * waits for (or on) an agent it polls every few seconds as the fallback for an
 * agent stream that is down or lagging — the live stream invalidates the roster
 * on every `agent.*` event itself (a `jentic register` lands as
 * `agent.self_registered`). An approved agent needs nothing more from the
 * roster, so the poll stops there.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { animate, useReducedMotionConfig } from 'framer-motion';
import { useQueryClient } from '@tanstack/react-query';
import { pendingAgentsCountKey } from '@/shared/hooks';
import { useAgentStreamOptional } from '@/shared/lib';
import type { SelectedApi } from '@/shared/credentials/api';
import {
	useAgentCredentialBindings,
	useAgents,
	type AgentEntity,
	type useApproveAgent,
	type useDenyAgent,
} from '@/modules/agents/api';
import { EASE_OUT_SOFT } from '@/modules/agents/components/flat/GhostFleet';
import { FIRST_AGENT_NAME } from '@/modules/agents/lib/agentName';
import { useRegisterName } from '@/modules/agents/lib/useRegisterName';
import {
	approvedCandidate,
	deriveLanding,
	dismissFirstRun,
	isFirstRunDismissed,
	resolveFirstRun,
	type FirstAgentExit,
	type FirstAgentPhase,
} from '@/modules/agents/lib/firstRun';

/** Roster poll while the landing waits: the stream's fallback. */
export const FIRST_AGENT_POLL_MS = 3_000;

/** The strip's tab list — the hand-off's destination and the focus target. */
const STRIP_TABLIST = '[role="tablist"][aria-label="Agents"]';

const NO_AGENTS: AgentEntity[] = [];

export interface FirstAgentLandingOptions {
	approve: Pick<ReturnType<typeof useApproveAgent>, 'variables' | 'isPending' | 'isSuccess'>;
	deny: Pick<ReturnType<typeof useDenyAgent>, 'variables' | 'isSuccess'>;
	/** Select an agent in the fleet view (`?agent=`). */
	selectAgent: (id: string, opts?: { replace?: boolean }) => void;
	/** Open the Add-APIs flow for an agent (`queue` skips the tray), or clear it. */
	openAddApis: (target: { agentId: string; queue: SelectedApi[] } | null) => void;
}

export function useFirstAgentLanding({
	approve,
	deny,
	selectAgent,
	openAddApis,
}: FirstAgentLandingOptions) {
	// Set once the org is seen with no fleet; cleared when the landing hands off.
	const [up, setUp] = useState(false);
	// The self-registered agent the landing is finishing. Tracked by id so it
	// stays the landing's agent after approval turns it active.
	const [trackedId, setTrackedId] = useState<string | null>(null);
	// The phase the poll was last gated on (the roster is read below it).
	const [pollPhase, setPollPhase] = useState<FirstAgentPhase | null>(null);
	const stream = useAgentStreamOptional();
	const polling = up && pollPhase !== 'approved' && stream?.status !== 'live';

	const query = useAgents({
		status: 'all',
		refetchInterval: polling ? FIRST_AGENT_POLL_MS : false,
	});
	const agents = useMemo(
		() => query.data?.pages.flatMap((p) => p.entities) ?? NO_AGENTS,
		[query.data],
	);

	const orgIsEmpty = !query.isPending && !query.error && agents.length === 0;
	useEffect(() => {
		if (orgIsEmpty) setUp(true);
	}, [orgIsEmpty]);

	// Where this mount resumes: decided once, from the whole roster and — for a
	// lone active agent whose suggestion wasn't dismissed — its bindings.
	const [resumed, setResumed] = useState(false);
	const rosterRead = !query.isPending && (!query.hasNextPage || query.isError);
	const candidate = !resumed && rosterRead ? approvedCandidate(agents) : null;
	const candidateBindings = useAgentCredentialBindings(
		candidate && !isFirstRunDismissed(candidate.id) ? candidate.id : null,
	);
	const resume =
		resumed || !rosterRead
			? null
			: // A later page failed: the roster is partial, so no first-run claim
				// about the org can be proven — the fleet (with its retry notice) is honest.
				query.isError
				? ({ kind: 'fleet' } as const)
				: resolveFirstRun({
						agents,
						bindings: candidateBindings.isError
							? { state: 'error' }
							: candidateBindings.data
								? { state: 'ready', count: candidateBindings.data.length }
								: { state: 'loading' },
						isDismissed: isFirstRunDismissed,
					});
	// Adjusted during render, not in an effect, so the frame after the reads
	// resolve is already the resolved view.
	if (!resumed && resume) {
		setResumed(true);
		if (resume.kind !== 'fleet') setUp(true);
		if (resume.kind === 'arrival' || resume.kind === 'approved') setTrackedId(resume.agentId);
	}

	const deniedId = deny.isSuccess ? (deny.variables?.id ?? null) : null;
	const view = up ? deriveLanding({ agents, trackedId, deniedId }) : null;
	const agent = view?.agent ?? null;
	const phase = view?.phase ?? null;
	if (phase !== pollPhase) setPollPhase(phase);

	// The name typed in the register card. Held here, so a denied agent's return
	// to listening keeps it and the New agent panel's manual form starts from it.
	const names = useMemo(() => agents.map((a) => a.name), [agents]);
	// The landing is up only while the org has no live fleet: its suggestion is
	// always "my-first-agent" (numbered past any archived or denied namesake).
	const nameDraft = useRegisterName({
		names,
		rosterRead,
		base: FIRST_AGENT_NAME,
		listening: phase !== 'arrived' && phase !== 'approved',
	});
	const { commandName } = nameDraft;
	// Whether this session showed the command at all. A mount that resumes
	// straight into an arrival never did, so there is no typed name to hold the
	// arrival against.
	const [commandShown, setCommandShown] = useState(false);
	if (phase === 'listening' && !commandShown) setCommandShown(true);

	const handOff = view?.handOff ?? false;
	useEffect(() => {
		if (handOff) setUp(false);
	}, [handOff]);

	const agentId = agent?.id ?? null;
	useEffect(() => {
		if (agentId != null) setTrackedId(agentId);
	}, [agentId]);
	// A pending arrival also feeds the nav badge, a separate query the fallback
	// poll does not refresh (a live stream refreshes it on its own).
	const queryClient = useQueryClient();
	useEffect(() => {
		if (agentId != null && polling)
			void queryClient.invalidateQueries({ queryKey: pendingAgentsCountKey });
	}, [agentId, polling, queryClient]);

	// Approve reads as in flight until the roster itself says active: the fleet
	// view the landing hands to must not show the agent as pending.
	const approving =
		agent != null &&
		approve.variables === agent.id &&
		(approve.isPending || (approve.isSuccess && agent.status === 'pending'));

	const visible = agents.length === 0 || up;

	// The hand-off's last beat: the agent's tab glides from where the preview's
	// slot sat to its place in the strip, and takes focus. Transform only;
	// reduced motion skips the glide.
	const reducedMotion = useReducedMotionConfig() ?? false;
	const slotRef = useRef<HTMLElement | null>(null);
	const handoffRef = useRef<{ from: DOMRect | null } | null>(null);
	useLayoutEffect(() => {
		const handoff = handoffRef.current;
		if (visible || !handoff) return;
		handoffRef.current = null;
		// The tab list, not the rail: its tabs and selection marker travel together
		// while the rail's own surface stays put.
		const tablist = document.querySelector<HTMLElement>(STRIP_TABLIST);
		const tab = tablist?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
		if (!tablist || !tab) return;
		tab.focus({ preventScroll: true });
		const { from } = handoff;
		if (!from) return;
		const to = tab.getBoundingClientRect();
		void animate(
			tablist,
			{ x: [from.left - to.left, 0], y: [from.top - to.top, 0] },
			{ duration: 0.56, ease: EASE_OUT_SOFT },
		);
	}, [visible]);

	function endLanding() {
		setUp(false);
		setTrackedId(null);
	}

	/** An exit from the card: the fleet, with the agent selected. Any exit from
	 * the suggestion is the operator's answer to it: a reload must not ask again. */
	function exit(to: FirstAgentExit) {
		if (!agent) return;
		handoffRef.current = {
			from: reducedMotion ? null : (slotRef.current?.getBoundingClientRect() ?? null),
		};
		selectAgent(agent.id, { replace: true });
		openAddApis(
			to.kind === 'skip'
				? null
				: { agentId: agent.id, queue: to.kind === 'queue' ? to.apis : [] },
		);
		dismissFirstRun(agent.id);
		endLanding();
	}

	/** "+N more waiting": the fleet, where every pending agent is listed. */
	function showFleet() {
		if (agent) selectAgent(agent.id);
		handoffRef.current = { from: null };
		endLanding();
	}

	/** An agent finished outside the landing (created by hand, or registered
	 * and approved from the New agent panel) ends it; a reload goes to the fleet,
	 * not back to a first-API suggestion for this agent. */
	function finishedElsewhere(id: string) {
		dismissFirstRun(id);
		endLanding();
	}

	return {
		query,
		agents,
		/** The resume decision is known (until then, neither view renders). */
		ready: resumed,
		visible,
		agent,
		morePending: view?.morePending ?? 0,
		approving,
		registerName: nameDraft.name,
		setRegisterName: nameDraft.setName,
		/** The name the displayed command registers with. */
		commandName,
		/** The existing agent name the typed one duplicates, or `null`. */
		registerNameDuplicateOf: nameDraft.duplicateOf,
		/** The name an arrival is expected to carry: the command's, once this
		 * session has shown it; otherwise unknown. */
		expectedName: commandShown ? commandName : null,
		/** The preview slot the hand-off glides from. */
		slotRef,
		exit,
		showFleet,
		finishedElsewhere,
	};
}
