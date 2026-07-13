/**
 * Dashboard repository tier.
 *
 * The ONLY place in the Dashboard module that talks to `@/shared/api` (the HTTP
 * facade) + the generated services. Views and hooks never import the facade
 * directly — ESLint enforces this (see ui/eslint.config.js "Layering"). Mirrors
 * the backend's Repository layer: thin wrappers that turn typed service calls
 * into UI-shaped overview slices and normalize errors into a single sentinel
 * the service tier can branch on.
 *
 * Dashboard has no endpoint of its own — each function here reads ONE existing
 * list endpoint cheaply (small page), and the composition happens at the hook /
 * component layer. We read across domains ONLY through the shared facade's
 * generated services, never by importing sibling modules.
 */
import {
	EventsService,
	ExecutionsService,
	ApIsService,
	ApiError,
	AgentsService,
	type AgentListResponse,
	type EventListResponse,
	type ExecutionListResponse,
} from '@/shared/api';
import {
	approxCountFromPage,
	deriveSuccessRate,
	type AlertsOverview,
	type CatalogOverview,
	type PendingAccessRequestsOverview,
	type PendingAgentsOverview,
	type RecentExecutionsOverview,
} from '@/modules/dashboard/api/types';
import { listAccessRequests, type AccessRequestPage } from '@/shared/lib';

/**
 * Sentinel error for Dashboard repository calls. Hooks/components branch on
 * `error instanceof DashboardApiError` without importing the generated
 * `ApiError` (which lives behind the facade). `status` is null for
 * network/parse failures that never reached the server.
 */
export class DashboardApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'DashboardApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toDashboardError(error: unknown, fallback: string): DashboardApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new DashboardApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new DashboardApiError(error.message || fallback, null, error);
	}
	return new DashboardApiError(fallback, null, error);
}

/** How many rows each overview slice samples — kept small (overview, not a list page). */
const OVERVIEW_PAGE_SIZE = 50;
const EXECUTIONS_SAMPLE = 25;

/**
 * Pending agents via `GET /agents?status=pending`. We keep a cheap page and
 * surface both the count (a floor when `has_more`) and a few rows to preview.
 */
export async function fetchPendingAgents(): Promise<PendingAgentsOverview> {
	try {
		const res: AgentListResponse = await AgentsService.listAgents({
			status: 'pending',
			limit: OVERVIEW_PAGE_SIZE,
		});
		return { count: approxCountFromPage(res), agents: res.data };
	} catch (error) {
		throw toDashboardError(error, 'Failed to load pending agents.');
	}
}

/**
 * Pending access requests via `GET /access-requests?status=pending`. This is
 * the DURABLE approval queue (unlike the rail's transient `access_request.filed`
 * events): the card surfaces the count + a few rows, and each row opens the
 * shared AccessRequestDialog to decide it. Reads through the cross-cutting
 * `@/shared/lib` access-request repository (the endpoint isn't on a generated
 * service yet — see shared/lib/accessRequests).
 */
export async function fetchPendingAccessRequests(): Promise<PendingAccessRequestsOverview> {
	try {
		const res = await listAccessRequests({ status: 'pending', limit: OVERVIEW_PAGE_SIZE });
		return { count: approxCountFromPage(res), requests: res.data };
	} catch (error) {
		throw toDashboardError(error, 'Failed to load pending access requests.');
	}
}

/**
 * One cursor page of access requests for the full-queue subpage
 * (`/app/access-requests`). Unlike `fetchPendingAccessRequests` (a small,
 * count-only overview slice for the card), this returns the raw cursor page so
 * the page can paginate with "Load more". `status` defaults to `pending`.
 */
export async function fetchAccessRequestsPage(params: {
	status?: string | null;
	cursor?: string | null;
	limit?: number;
}): Promise<AccessRequestPage> {
	try {
		return await listAccessRequests({
			status: params.status ?? 'pending',
			cursor: params.cursor ?? null,
			limit: params.limit ?? 25,
		});
	} catch (error) {
		throw toDashboardError(error, 'Failed to load access requests.');
	}
}

/**
 * Actionable events via `GET /events?requires_action=true`. These are the
 * alerts that need a human — the card lists them and links into Monitor.
 */
export async function fetchActionableEvents(): Promise<AlertsOverview> {
	try {
		const res: EventListResponse = await EventsService.listEvents({
			requiresAction: true,
			limit: OVERVIEW_PAGE_SIZE,
		});
		return { count: approxCountFromPage(res), events: res.data };
	} catch (error) {
		throw toDashboardError(error, 'Failed to load alerts.');
	}
}

/**
 * Recent executions via `GET /executions`. We sample a small page and derive
 * the success rate client-side (there is no aggregate stats endpoint).
 */
export async function fetchRecentExecutions(): Promise<RecentExecutionsOverview> {
	try {
		const res: ExecutionListResponse = await ExecutionsService.listExecutions({
			limit: EXECUTIONS_SAMPLE,
		});
		return {
			executions: res.data,
			successRate: deriveSuccessRate(res.data),
			sampled: res.data.length,
		};
	} catch (error) {
		throw toDashboardError(error, 'Failed to load recent executions.');
	}
}

/**
 * Catalog size via `GET /apis`. The committed contract is cursor-paginated with
 * no aggregate `total`, so this is an approximate count (page length, "N+" when
 * `has_more`). MINOR backend gap — see STATUS.md / API-CONTRACT.md; we do NOT
 * build a speculative `GET /stats`.
 */
export async function fetchCatalogSize(): Promise<CatalogOverview> {
	try {
		const res = await ApIsService.listApis({ limit: OVERVIEW_PAGE_SIZE });
		// The generated service returns `any` here (foundation codegen); narrow to
		// the cursor-paginated envelope we rely on.
		const page = res as { data: unknown[]; has_more: boolean };
		return { apiCount: approxCountFromPage(page) };
	} catch (error) {
		throw toDashboardError(error, 'Failed to load the API catalog size.');
	}
}
