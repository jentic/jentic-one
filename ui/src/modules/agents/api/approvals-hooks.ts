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
	decideApproval,
	getApproval,
	listApprovals,
	type ListApprovalsParams,
} from '@/modules/agents/api/approvals-client';
import { sharedQueryKeys, type DecideRequest, type ExecutionApprovalResponse } from '@/shared/api';

export { ApprovalDecision, ExecutionApprovalState } from '@/shared/api';
export { ApprovalsApiError } from '@/modules/agents/api/approvals-client';

/** Stable query-key roots for precise cache invalidation. */
export const approvalsKeys = {
	all: sharedQueryKeys.approvalsRoot,
	list: (params: ListApprovalsParams) => [...approvalsKeys.all, 'list', params] as const,
	detail: (id: string) => [...approvalsKeys.all, 'detail', id] as const,
};

/** List approvals with optional state / agent filter. */
export function useApprovals(params: ListApprovalsParams = {}) {
	return useQuery({
		queryKey: approvalsKeys.list(params),
		queryFn: () => listApprovals(params),
	});
}

/** Load a single approval by id. */
export function useApproval(approvalId: string | undefined) {
	return useQuery({
		queryKey: approvalsKeys.detail(approvalId ?? ''),
		queryFn: () => getApproval(approvalId!),
		enabled: !!approvalId,
	});
}

/**
 * Approve or deny a pending approval. Invalidates the Approvals slices, the
 * waiting signals (inbox, badges, "Waiting for you") and the held job's
 * Monitor rows on success.
 */
export function useDecideApproval() {
	const qc = useQueryClient();
	return useMutation<
		ExecutionApprovalResponse,
		Error,
		{ approvalId: string; body: DecideRequest }
	>({
		mutationFn: ({ approvalId, body }) => decideApproval(approvalId, body),
		onSuccess: (data, { approvalId }) => {
			qc.invalidateQueries({ queryKey: approvalsKeys.all });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot });
			qc.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobsRoot });
			qc.invalidateQueries({ queryKey: [...sharedQueryKeys.monitorJobRoot, data.job_id] });
			qc.setQueryData(approvalsKeys.detail(approvalId), data);
			const verb = data.state === 'approved' ? 'Approved' : 'Denied';
			toast({
				title: `${verb} execution for ${data.api_vendor}/${data.api_name}`,
				variant: 'success',
			});
		},
		onError: (error) => {
			toast({
				title: error.message ?? 'Failed to submit decision',
				variant: 'error',
			});
		},
	});
}
