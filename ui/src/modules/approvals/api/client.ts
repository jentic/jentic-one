/**
 * Approvals repository tier.
 *
 * The ONLY place in the Approvals module that talks to `@/shared/api` (the HTTP
 * facade). Views and hooks never import the facade directly — ESLint enforces
 * this. Mirrors the backend Repository layer: thin wrappers that turn typed
 * service calls into UI-shaped data and normalize errors into a single sentinel.
 *
 * Response-code contract:
 *   GET  /execution-approvals           → 200 + ExecutionApprovalListResponse
 *   GET  /execution-approvals/{id}      → 200 + ExecutionApprovalResponse
 *   POST /execution-approvals/{id}:decide → 200 + ExecutionApprovalResponse
 */
import {
	ApiError,
	ExecutionApprovalsService,
	type DecideRequest,
	type ExecutionApprovalListResponse,
	type ExecutionApprovalResponse,
} from '@/shared/api';

// Re-export types so module pages never need to reach into @/shared/api directly.
export type { DecideRequest, ExecutionApprovalListResponse, ExecutionApprovalResponse };

/** Sentinel error for Approvals repository calls. */
export class ApprovalsApiError extends Error {
	readonly status: number | null;
	readonly cause?: unknown;

	constructor(message: string, status: number | null, cause?: unknown) {
		super(message);
		this.name = 'ApprovalsApiError';
		this.status = status;
		this.cause = cause;
	}
}

function toApprovalsError(error: unknown, fallback: string): ApprovalsApiError {
	if (error instanceof ApiError) {
		const detail = (error.body as { detail?: string } | undefined)?.detail ?? error.message;
		return new ApprovalsApiError(detail || fallback, error.status, error);
	}
	if (error instanceof Error) {
		return new ApprovalsApiError(error.message || fallback, null, error);
	}
	return new ApprovalsApiError(fallback, null, error);
}

export interface ListApprovalsParams {
	state?: string | null;
	agentId?: string | null;
	cursor?: string | null;
	limit?: number;
}

export async function listApprovals(
	params: ListApprovalsParams = {},
): Promise<ExecutionApprovalListResponse> {
	try {
		return await ExecutionApprovalsService.listExecutionApprovals(params);
	} catch (error) {
		throw toApprovalsError(error, 'Failed to list approvals');
	}
}

export async function getApproval(approvalId: string): Promise<ExecutionApprovalResponse> {
	try {
		return await ExecutionApprovalsService.getExecutionApproval({ approvalId });
	} catch (error) {
		throw toApprovalsError(error, 'Failed to load approval');
	}
}

export async function decideApproval(
	approvalId: string,
	body: DecideRequest,
): Promise<ExecutionApprovalResponse> {
	try {
		return await ExecutionApprovalsService.decideExecutionApproval({
			approvalId,
			requestBody: body,
		});
	} catch (error) {
		throw toApprovalsError(error, 'Failed to submit decision');
	}
}
