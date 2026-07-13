/**
 * Dashboard module — UI-facing types + pure derivations.
 *
 * The Dashboard has NO backend of its own: it composes an overview client-side
 * from four existing list endpoints (agents, events, executions, apis). This
 * file holds the small UI envelopes each composed slice resolves to, plus the
 * pure functions that derive the headline numbers (counts, success rate) from
 * the raw list responses. Keeping the math here (not in components or hooks)
 * makes it unit-testable in isolation and keeps the layers thin.
 */
import type { AgentResponse, EventResponse, ExecutionResponse } from '@/shared/api';
import type { AccessRequest } from '@/shared/lib';

/**
 * A count that may be a floor rather than an exact total. The list endpoints
 * we compose are cursor-paginated and carry no aggregate `total`, so a single
 * cheap page gives us either the exact count (when the page wasn't full /
 * `has_more` is false) or a lower bound (`atLeast`) we render as "N+".
 */
export interface ApproxCount {
	value: number;
	/** True when more rows exist beyond the page we counted (render "N+"). */
	atLeast: boolean;
}

/** Pending-agents overview slice. */
export interface PendingAgentsOverview {
	count: ApproxCount;
	/** A few representative agents to preview in the card. */
	agents: AgentResponse[];
}

/** Actionable-events overview slice. */
export interface AlertsOverview {
	count: ApproxCount;
	events: EventResponse[];
}

/** Pending access-requests overview slice (the durable approval queue). */
export interface PendingAccessRequestsOverview {
	count: ApproxCount;
	/** A few representative requests to preview in the card. */
	requests: AccessRequest[];
}

/** Recent-executions overview slice, with a derived success rate. */
export interface RecentExecutionsOverview {
	executions: ExecutionResponse[];
	/** Successes / total over the sampled page; null when nothing sampled. */
	successRate: number | null;
	/** Count of executions sampled (the page size we actually saw). */
	sampled: number;
}

/** Catalog-size overview slice (workspace-registered APIs). */
export interface CatalogOverview {
	apiCount: ApproxCount;
}

/**
 * Whether an execution counts as "successful" for the success-rate metric.
 *
 * The execution `status` is `status: string` on the wire contract (the typed
 * `ExecutionStatus` enum is server-side only). We treat an HTTP 2xx, or a
 * status of `completed`/`succeeded`/`success`, as a success, and anything else
 * (failed/error/running/…) as not. This is intentionally lenient so a contract
 * tweak doesn't silently zero the rate.
 */
export function isSuccessfulExecution(execution: ExecutionResponse): boolean {
	const http = execution.http_status;
	if (typeof http === 'number') return http >= 200 && http < 300;
	const status = (execution.status ?? '').toLowerCase();
	return status === 'completed' || status === 'succeeded' || status === 'success';
}

/** Success rate over a page of executions, or null when the page is empty. */
export function deriveSuccessRate(executions: ExecutionResponse[]): number | null {
	if (executions.length === 0) return null;
	const ok = executions.filter(isSuccessfulExecution).length;
	return ok / executions.length;
}

/**
 * Turn a `{ data, has_more }` page into an `ApproxCount`. When `has_more` is
 * true we only know a lower bound, so the UI renders "N+".
 */
export function approxCountFromPage<T>(page: { data: T[]; has_more: boolean }): ApproxCount {
	return { value: page.data.length, atLeast: page.has_more };
}

/** Format an `ApproxCount` for display, e.g. `0`, `3`, or `50+`. */
export function formatApproxCount(count: ApproxCount): string {
	return count.atLeast ? `${count.value}+` : `${count.value}`;
}
