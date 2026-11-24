/**
 * The order the review pop-up walks the viewer's waiting calls in: the open
 * call's agent first, then everyone else, each oldest first (the order
 * `usePendingApprovals` already returns). Its stepper reads "2 of 7" over this
 * queue, and a decision moves on to the next call in it, so the viewer finishes
 * one agent's calls before moving to the next agent. Scoped to one agent (an
 * agent's "Review all"), the queue is that agent's calls alone.
 */
import type { HeldExecutionResponse } from '@/shared/api';

type QueuedCall = Pick<HeldExecutionResponse, 'id' | 'agent_id'>;

/** `pending` (oldest first) reordered with `agentId`'s calls leading — or,
 * `only` that agent's, nobody else's. */
export function reviewQueue<T extends QueuedCall>(
	pending: readonly T[],
	agentId: string,
	only = false,
): T[] {
	const own = pending.filter((call) => call.agent_id === agentId);
	return only ? own : [...own, ...pending.filter((call) => call.agent_id !== agentId)];
}

/**
 * The call to open after `current` is decided: the same agent's oldest other
 * waiting call, else (unless scoped to the agent) the oldest waiting call of
 * anyone. `skip` holds calls already decided in this sitting that the list may
 * still show until it refreshes.
 */
export function nextWaitingCall<T extends QueuedCall>(
	pending: readonly T[],
	current: QueuedCall,
	skip: ReadonlySet<string> = new Set(),
	onlyAgent = false,
): T | undefined {
	const rest = pending.filter((call) => call.id !== current.id && !skip.has(call.id));
	return reviewQueue(rest, current.agent_id, onlyAgent)[0];
}
