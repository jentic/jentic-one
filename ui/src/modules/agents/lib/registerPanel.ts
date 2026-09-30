/**
 * The New agent panel's "Register from the CLI" rules: which agent the panel
 * is finishing. Pure; the state lives in `useRegisterPanel`.
 *
 * The panel opens over an existing fleet, whose pending agents are already
 * listed (the approval banner). So an arrival is only an agent that registered
 * after this opening of the panel — its `createdAt` is past the opening, and
 * the roster did not already hold it then. An older pending agent is ignored
 * here, neither picked nor counted. Of several arrivals the one carrying the
 * command's name wins, then the newest. Once shown, the pick is tracked by id,
 * so it never swaps under the operator and stays the panel's after approval
 * turns it active.
 */
import type { AgentEntity } from '@/modules/agents/api';
import type { FirstAgentPhase } from '@/modules/agents/lib/firstRun';

/**
 * How far before the opening an agent's `createdAt` may be and still count as
 * an arrival. `createdAt` is the server's clock and the opening the browser's:
 * a strict comparison would hide a `jentic register` run the moment the panel
 * opened on a browser a little ahead of the server. An agent inside the window
 * that the roster already held at the opening is still not an arrival.
 */
export const ARRIVAL_CLOCK_SKEW_MS = 15_000;

export interface PanelArrival {
	/** The agent the panel is finishing: pending or active, never the denied one. */
	agent: AgentEntity | null;
	phase: FirstAgentPhase;
	/** Other arrivals waiting for approval besides `agent`. */
	morePending: number;
}

export function derivePanelArrival(input: {
	agents: AgentEntity[];
	/** When the panel opened (`Date.now()`). */
	openedAt: number;
	/** The agents the roster held at the opening. */
	knownAtOpen: ReadonlySet<string>;
	trackedId: string | null;
	deniedId: string | null;
	/** The name the displayed command registers with. */
	expectedName: string;
}): PanelArrival {
	const { agents, openedAt, knownAtOpen, trackedId, deniedId, expectedName } = input;
	const tracked = agents.find((a) => a.id === trackedId);
	const trackedLive =
		tracked &&
		tracked.id !== deniedId &&
		(tracked.status === 'pending' || tracked.status === 'active')
			? tracked
			: null;
	const since = openedAt - ARRIVAL_CLOCK_SKEW_MS;
	const arrivals = agents
		.filter(
			(a) =>
				a.status === 'pending' &&
				a.id !== deniedId &&
				!knownAtOpen.has(a.id) &&
				Date.parse(a.createdAt) >= since,
		)
		.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
	const agent =
		trackedLive ?? arrivals.find((a) => a.name === expectedName) ?? arrivals[0] ?? null;
	const morePending = agent ? arrivals.filter((a) => a.id !== agent.id).length : 0;
	return {
		agent,
		phase: agent == null ? 'listening' : agent.status === 'pending' ? 'arrived' : 'approved',
		morePending,
	};
}
