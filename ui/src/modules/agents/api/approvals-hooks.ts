/**
 * Approvals service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for Approvals views: pages call these hooks,
 * which call the repository (`./client`), which calls `@/shared/api`. Views
 * must never reach past this layer (ESLint-enforced). Mirrors the backend
 * Service layer.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	ApprovalsApiError,
	decideApproval,
	getApproval,
	listApprovals,
	type HeldExecutionDetailResponse,
	type ListApprovalsParams,
} from '@/modules/agents/api/approvals-client';
import { isCallUnderWay } from '@/modules/agents/lib/approvalState';
import {
	sharedQueryKeys,
	type DecideHeldExecutionRequest,
	type HeldExecutionResponse,
} from '@/shared/api';

export { ApprovalDecision, ExecutionApprovalState } from '@/shared/api';
export { ApprovalsApiError, HELD_EXECUTION_ERRORS } from '@/modules/agents/api/approvals-client';

/** How often the review page re-reads a call that is approved but not yet finished. */
const CALL_POLL_MS = 3_000;

/** Stable query-key roots for precise cache invalidation. */
export const approvalsKeys = {
	all: sharedQueryKeys.approvalsRoot,
	list: (params: ListApprovalsParams) => [...approvalsKeys.all, 'list', params] as const,
	detail: (id: string) => [...approvalsKeys.all, 'detail', id] as const,
};

/** List held executions with optional state / agent filter. */
export function useApprovals(params: ListApprovalsParams = {}) {
	return useQuery({
		queryKey: approvalsKeys.list(params),
		queryFn: () => listApprovals(params),
	});
}

/** Load a single held execution by id. */
export function useApproval(heldExecutionId: string | undefined) {
	return useQuery({
		queryKey: approvalsKeys.detail(heldExecutionId ?? ''),
		queryFn: () => getApproval(heldExecutionId!),
		enabled: !!heldExecutionId,
		// A decided call moves on (queued → running → its outcome) without an
		// event the page can count on, so poll while it is still under way.
		refetchInterval: (query) =>
			isCallUnderWay(query.state.data?.state, query.state.data?.execution_status)
				? CALL_POLL_MS
				: false,
	});
}

/**
 * Approve or deny a pending held execution. Invalidates the Approvals slices,
 * the waiting signals (inbox, badges, "Waiting for you") and the Monitor job
 * rows on success. The detail slice is refetched rather than overwritten: the
 * decide response is the list shape, without the held request.
 */
export function useDecideApproval() {
	const qc = useQueryClient();
	return useMutation<
		HeldExecutionResponse,
		Error,
		{ heldExecutionId: string; body: DecideHeldExecutionRequest }
	>({
		mutationFn: ({ heldExecutionId, body }) => decideApproval(heldExecutionId, body),
		onSuccess: (data, { heldExecutionId }) => {
			qc.setQueryData<HeldExecutionDetailResponse>(
				approvalsKeys.detail(heldExecutionId),
				(prev) => (prev ? { ...prev, ...data } : prev),
			);
			qc.invalidateQueries({ queryKey: approvalsKeys.all });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobsRoot });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobRoot });
			const verb = data.state === 'approved' ? 'Approved' : 'Denied';
			toast({
				title: `${verb} execution for ${data.api_vendor}/${data.api_name}`,
				variant: 'success',
			});
		},
		onError: (error) => {
			// The review page explains a refused decision in place.
			if (error instanceof ApprovalsApiError && error.status === 403) return;
			toast({
				title: error.message ?? 'Failed to submit decision',
				variant: 'error',
			});
		},
	});
}
