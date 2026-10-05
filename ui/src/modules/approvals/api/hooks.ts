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
} from '@/modules/approvals/api/client';
import type { DecideRequest, ExecutionApprovalResponse } from '@/shared/api';

/** Stable query-key roots for precise cache invalidation. */
export const approvalsKeys = {
	all: ['approvals'] as const,
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

/** Approve or deny a pending approval. Invalidates list + detail on success. */
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
