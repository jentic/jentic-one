/**
 * Approvals repository tier.
 *
 * The ONLY place in the Approvals module that talks to `@/shared/api` (the HTTP
 * facade). Views and hooks never import the facade directly — ESLint enforces
 * this. Mirrors the backend Repository layer: thin wrappers that turn typed
 * service calls into UI-shaped data and normalize errors into a single sentinel.
 *
 * Response-code contract (the held-executions surface):
 *   GET  /executions/holds              → 200 + HeldExecutionListResponse
 *   GET  /executions/holds/{id}         → 200 + HeldExecutionDetailResponse
 *   POST /executions/holds/{id}:decide  → 200 + HeldExecutionResponse
 *   404 on detail/decide means the held execution is outside the caller's
 *   visibility (the filing agent, the agent's owner, or an org admin) — or it
 *   does not exist. 403 `held_execution_decide_forbidden` on decide means the
 *   caller sees it but may not decide it (only the owner or an org admin may).
 *   409 `held_execution_already_settled` means it is no longer pending.
 */
import {
	ApiError,
	HeldExecutionsService,
	type DecideHeldExecutionRequest,
	type HeldExecutionListResponse,
	type HeldExecutionDetailResponse,
	type HeldExecutionResponse,
	type ExecutionApprovalState,
} from '@/shared/api';

// Re-export types so module pages never need to reach into @/shared/api directly.
export type {
	DecideHeldExecutionRequest,
	HeldExecutionDetailResponse,
	HeldExecutionListResponse,
	HeldExecutionResponse,
};

/** The held-executions problem `type`s the Approvals pages tell apart. */
export const HELD_EXECUTION_ERRORS = {
	notFound: 'held_execution_not_found',
	alreadySettled: 'held_execution_already_settled',
	notHeld: 'held_execution_not_held',
	decideForbidden: 'held_execution_decide_forbidden',
} as const;

/** Sentinel error for Approvals repository calls. */
export class ApprovalsApiError extends Error {
	readonly status: number | null;
	/** The problem body's `type` (e.g. `held_execution_decide_forbidden`), when it has one. */
	readonly code: string | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown, code?: string | null) {
		super(message);
		this.name = 'ApprovalsApiError';
		this.status = status;
		this.code = code ?? null;
		this.cause = cause;
	}

	/** Whether the problem `type` is `code`, bare or as the last segment of a type URI. */
	is(code: string): boolean {
		return this.code === code || (this.code?.endsWith(`/${code}`) ?? false);
	}
}

function toApprovalsError(error: unknown, fallback: string): ApprovalsApiError {
	if (error instanceof ApiError) {
		const body = error.body as { detail?: unknown; type?: unknown } | undefined;
		const detail = typeof body?.detail === 'string' ? body.detail : error.message;
		const code = typeof body?.type === 'string' ? body.type : null;
		return new ApprovalsApiError(detail || fallback, error.status, error, code);
	}
	if (error instanceof Error) {
		return new ApprovalsApiError(error.message || fallback, null, error);
	}
	return new ApprovalsApiError(fallback, null, error);
}

export interface ListApprovalsParams {
	state?: ExecutionApprovalState | null;
	agentId?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listApprovals(
	params: ListApprovalsParams = {},
): Promise<HeldExecutionListResponse> {
	try {
		return await HeldExecutionsService.listHeldExecutions(params);
	} catch (error) {
		throw toApprovalsError(error, 'Failed to list approvals');
	}
}

export async function getApproval(heldExecutionId: string): Promise<HeldExecutionDetailResponse> {
	try {
		return await HeldExecutionsService.getHeldExecution({ heldExecutionId });
	} catch (error) {
		throw toApprovalsError(error, 'Failed to load approval');
	}
}

export async function decideApproval(
	heldExecutionId: string,
	body: DecideHeldExecutionRequest,
): Promise<HeldExecutionResponse> {
	try {
		return await HeldExecutionsService.decideHeldExecution({
			heldExecutionId,
			requestBody: body,
		});
	} catch (error) {
		throw toApprovalsError(error, 'Failed to submit decision');
	}
}
