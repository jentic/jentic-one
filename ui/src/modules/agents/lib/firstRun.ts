/**
 * The zero-agents landing's rules: where the Agents page resumes on load, and
 * what the landing shows in-session. The page's hook (`useFirstAgentLanding`)
 * owns the state; everything here is pure.
 *
 * Resume, decided once per page mount from the roster (and, for a lone active
 * agent, its bindings):
 * - no fleet at all             → the landing, listening;
 * - nothing working, ≥1 pending → the landing's arrival, for the newest
 *   pending agent (the rest are counted, so the card can point at them);
 * - exactly one agent, active, no API bindings, first-run not dismissed
 *   → the landing's approved state (the first-API suggestion);
 * - otherwise                   → the fleet view.
 *
 * "Working" is active or disabled — the same test the in-session landing uses
 * to hand off to the fleet — and denied or archived agents are history, not
 * fleet, so an operator who denied a stray registration still resumes.
 */
import type { AgentEntity } from '@/modules/agents/api';
import type { SelectedApi } from '@/shared/credentials/api';

/** The landing card's state: waiting, an agent to approve, or an approved one. */
export type FirstAgentPhase = 'listening' | 'arrived' | 'approved';

/** How the operator leaves the landing. */
export type FirstAgentExit =
	/** Straight to the fleet view. */
	| { kind: 'skip' }
	/** The Add-APIs tray, nothing ticked. */
	| { kind: 'tray' }
	/** Straight into the setup queue for these APIs — no re-pick in the tray. */
	| { kind: 'queue'; apis: SelectedApi[] };

export type FirstRunResume =
	| { kind: 'fleet' }
	| { kind: 'listening' }
	| { kind: 'arrival'; agentId: string }
	| { kind: 'approved'; agentId: string };

/** What the decision needs about the lone active agent's bindings. */
export type BindingsRead =
	{ state: 'loading' } | { state: 'error' } | { state: 'ready'; count: number };

const isHistory = (a: AgentEntity): boolean => a.status === 'rejected' || a.status === 'archived';
const isWorking = (a: AgentEntity): boolean => a.status === 'active' || a.status === 'disabled';

/** The newest pending agent that isn't `exclude`d, or null. */
function newestPending(agents: AgentEntity[], exclude: string | null = null): AgentEntity | null {
	let best: AgentEntity | null = null;
	for (const a of agents) {
		if (a.status !== 'pending' || a.id === exclude) continue;
		if (!best || a.createdAt > best.createdAt) best = a;
	}
	return best;
}

/** The one agent the approved-state rule could resume for, or null. Its
 * bindings must then be read before the decision is known. */
export function approvedCandidate(agents: AgentEntity[]): AgentEntity | null {
	const fleet = agents.filter((a) => !isHistory(a));
	const only = fleet.length === 1 ? fleet[0] : undefined;
	return only && only.status === 'active' ? only : null;
}

/**
 * The resume decision, or `null` while it isn't known yet (the candidate's
 * bindings still loading). `agents` must be the whole roster.
 */
export function resolveFirstRun(input: {
	agents: AgentEntity[];
	bindings: BindingsRead;
	isDismissed: (agentId: string) => boolean;
}): FirstRunResume | null {
	const { agents, bindings, isDismissed } = input;
	const fleet = agents.filter((a) => !isHistory(a));
	if (fleet.length === 0) return { kind: 'listening' };
	if (!fleet.some(isWorking)) {
		const pending = newestPending(fleet);
		if (pending) return { kind: 'arrival', agentId: pending.id };
	}
	const candidate = approvedCandidate(agents);
	if (!candidate || isDismissed(candidate.id)) return { kind: 'fleet' };
	if (bindings.state === 'loading') return null;
	// A failed read can't prove the agent has no APIs: the fleet is the safe side.
	if (bindings.state === 'error' || bindings.count > 0) return { kind: 'fleet' };
	return { kind: 'approved', agentId: candidate.id };
}

// ── In session ──────────────────────────────────────────────────────────────

export interface LandingView {
	/** The agent the card is finishing: pending or active, never the denied one. */
	agent: AgentEntity | null;
	phase: FirstAgentPhase;
	/** Other agents waiting for approval besides `agent`. */
	morePending: number;
	/** A working agent that is not the landing's own (created in another tab, or
	 * by a teammate): the org is set up, so the fleet view takes over. */
	handOff: boolean;
}

/**
 * What the landing shows for the current roster. The tracked agent stays the
 * landing's while it is pending or active (so approval keeps it on the card);
 * otherwise it is the newest pending arrival. A denied agent is dropped — as
 * soon as the deny settles, not only once the roster refetch catches up — so
 * the card goes back to listening.
 */
export function deriveLanding(input: {
	agents: AgentEntity[];
	trackedId: string | null;
	deniedId: string | null;
}): LandingView {
	const { agents, trackedId, deniedId } = input;
	const tracked = agents.find((a) => a.id === trackedId);
	const trackedLive =
		tracked &&
		tracked.id !== deniedId &&
		(tracked.status === 'pending' || tracked.status === 'active')
			? tracked
			: null;
	const agent = trackedLive ?? newestPending(agents, deniedId);
	const morePending = agent
		? agents.filter((a) => a.status === 'pending' && a.id !== agent.id && a.id !== deniedId)
				.length
		: 0;
	return {
		agent,
		phase: agent == null ? 'listening' : agent.status === 'pending' ? 'arrived' : 'approved',
		morePending,
		handOff: agent == null && agents.some(isWorking),
	};
}

// ── Dismissal ───────────────────────────────────────────────────────────────

/** Namespaced like the app's other UI prefs (`j1.updateBanner.…`). */
export const firstRunDismissedKey = (agentId: string): string =>
	`j1.agents.firstRun.dismissed.${agentId}`;

export function isFirstRunDismissed(agentId: string): boolean {
	try {
		return window.localStorage.getItem(firstRunDismissedKey(agentId)) != null;
	} catch {
		return false;
	}
}

/** The operator left the first-run suggestion for this agent: don't bring it
 * back on a reload. Storage failures are ignored — worst case it reappears. */
export function dismissFirstRun(agentId: string): void {
	try {
		window.localStorage.setItem(firstRunDismissedKey(agentId), new Date().toISOString());
	} catch {
		// Private mode / quota — nothing to do.
	}
}
