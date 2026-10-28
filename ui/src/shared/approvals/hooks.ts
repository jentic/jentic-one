/**
 * Approvals service tier — TanStack Query hooks.
 *
 * The ONLY backend access path for the held-call surfaces: views call these
 * hooks, which call the repository (`./client`), which calls `@/shared/api`.
 * Views must never reach past this layer (ESLint-enforced). Mirrors the
 * backend Service layer.
 */
import { useCallback } from 'react';
import {
	useMutation,
	useQueries,
	useQuery,
	useQueryClient,
	type QueryClient,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import { pendingApprovalsKey, type PendingApprovals } from '@/shared/approvals/api';
import {
	decideApproval,
	getApproval,
	isAlreadySettled,
	isDecideForbidden,
	listOrgAdmins,
	type OrgAdmins,
} from '@/shared/approvals/client';
import { isCallUnderWay } from '@/shared/approvals/approvalState';
import type { HeldCallDetail } from '@/shared/approvals/heldCall';
import {
	sharedQueryKeys,
	type DecideHeldExecutionRequest,
	type HeldExecutionResponse,
} from '@/shared/api';

export { ApprovalDecision, HeldExecutionState } from '@/shared/api';
export {
	ApprovalsApiError,
	HELD_EXECUTION_ERRORS,
	isAlreadySettled,
	isDecideForbidden,
	type OrgAdmin,
	type OrgAdmins,
} from '@/shared/approvals/client';

/** How often the review surfaces re-read a call that is approved but not yet finished. */
const CALL_POLL_MS = 3_000;

/**
 * A detail just read is fresh for a moment: the pop-up landing on a call the
 * walk re-read a breath ago doesn't read it again on mount.
 */
const DETAIL_FRESH_MS = 2_000;

/** The org's admins change rarely; re-read them at most every 5 minutes. */
const ORG_ADMINS_STALE_MS = 5 * 60_000;

/** Stable query-key roots for precise cache invalidation. */
export const approvalsKeys = {
	all: sharedQueryKeys.approvalsRoot,
	detail: (id: string) => [...approvalsKeys.all, 'detail', id] as const,
};

/**
 * Outside the approvals root: a decision invalidates that root, and who can
 * decide doesn't change with one.
 */
const orgAdminsKey = ['held-call-deciders', 'org-admins'] as const;

function detailQuery(heldExecutionId: string, poll: boolean) {
	return {
		queryKey: approvalsKeys.detail(heldExecutionId),
		queryFn: () => getApproval(heldExecutionId),
		staleTime: DETAIL_FRESH_MS,
		// A decided call moves on (queued → running → its outcome) without an
		// event the surface can count on, so poll while it is still under way.
		refetchInterval: (query: { state: { data?: HeldCallDetail } }) =>
			poll && isCallUnderWay(query.state.data?.state, query.state.data?.execution_status)
				? CALL_POLL_MS
				: false,
	};
}

/**
 * Load a single held execution by id, with the held request and the agent's
 * reason when it sent one. Pass `poll: false` for a read that only needs who
 * decided it (a receipt), not where the approved call stands.
 */
export function useApproval(heldExecutionId: string | undefined, options?: { poll?: boolean }) {
	return useQuery({
		...detailQuery(heldExecutionId ?? '', options?.poll ?? true),
		enabled: !!heldExecutionId,
	});
}

/**
 * Load several held executions at once (the bulk-approve confirmation's lines).
 * Pass `enabled: false` to keep what is loaded without re-reading it: a run of
 * decisions invalidates the approvals root after each one.
 */
export function useApprovalDetails(
	heldExecutionIds: readonly string[],
	options?: { enabled?: boolean },
) {
	return useQueries({
		queries: heldExecutionIds.map((id) => ({
			...detailQuery(id, false),
			enabled: options?.enabled ?? true,
		})),
	});
}

/**
 * Read one held call now, fresh from the server, for a surface deciding where
 * to go next (the pop-up checks a call still waits before moving on to it).
 */
export function useEnsureApproval() {
	const qc = useQueryClient();
	return useCallback(
		(heldExecutionId: string) =>
			qc.fetchQuery({
				queryKey: approvalsKeys.detail(heldExecutionId),
				queryFn: () => getApproval(heldExecutionId),
				staleTime: 0,
			}),
		[qc],
	);
}

/** Re-read every surface a decision changes: the lists, the waiting signals, the Monitor's jobs. */
function refreshSurfaces(qc: QueryClient): void {
	void qc.invalidateQueries({ queryKey: approvalsKeys.all });
	void qc.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot });
	void qc.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobsRoot });
	void qc.invalidateQueries({ queryKey: sharedQueryKeys.monitorJobRoot });
}

/** For a run of `batch` decisions: one refresh of every surface when the run ends. */
export function useRefreshApprovalSurfaces() {
	const qc = useQueryClient();
	return useCallback(() => refreshSurfaces(qc), [qc]);
}

/** The decided call leaves the waiting list at once, ahead of the list's re-read. */
function dropFromPending(qc: QueryClient, heldExecutionId: string): void {
	qc.setQueryData<PendingApprovals>(pendingApprovalsKey, (prev) =>
		prev
			? { ...prev, approvals: prev.approvals.filter((row) => row.id !== heldExecutionId) }
			: prev,
	);
}

/**
 * The org's admins, for the "who can decide" line. Only an org admin may list
 * every user, so pass `enabled: false` for anyone else: no request goes out.
 */
export function useOrgAdmins(enabled: boolean) {
	return useQuery<OrgAdmins>({
		queryKey: orgAdminsKey,
		queryFn: listOrgAdmins,
		enabled,
		staleTime: ORG_ADMINS_STALE_MS,
		retry: false,
	});
}

export interface DecideApprovalOptions {
	/**
	 * Skip the per-decision toasts (success and failure). For a surface that
	 * decides several calls in a row and reports the outcome once itself: the
	 * review pop-up moving on to the next call, or a bulk action.
	 */
	quiet?: boolean;
	/**
	 * Leave the lists alone after each decision: the caller runs several in a
	 * row and refreshes once at the end (`useRefreshApprovalSurfaces`), so N
	 * decisions don't re-read every surface N times.
	 */
	batch?: boolean;
}

/**
 * Approve or deny a pending held execution. Invalidates the Approvals slices,
 * the waiting signals (bell, Inbox, badges, the waiting card) and the Monitor job
 * rows on success, and the Approvals slices and waiting signals when the call
 * was already settled (so it leaves every list). The detail slice is refetched
 * rather than overwritten: the decide response is the list shape, without the
 * held request.
 */
export function useDecideApproval(options: DecideApprovalOptions = {}) {
	const { quiet = false, batch = false } = options;
	const qc = useQueryClient();
	return useMutation<
		HeldExecutionResponse,
		Error,
		{ heldExecutionId: string; body: DecideHeldExecutionRequest }
	>({
		mutationFn: ({ heldExecutionId, body }) => decideApproval(heldExecutionId, body),
		onSuccess: (data, { heldExecutionId }) => {
			qc.setQueryData<HeldCallDetail>(approvalsKeys.detail(heldExecutionId), (prev) => {
				if (!prev) return prev;
				// The list shape leaves what only the detail knows null (the held
				// request, the execution once it runs): keep the detail's own.
				const patch = Object.fromEntries(
					Object.entries(data).filter(([, value]) => value != null),
				);
				return { ...prev, ...patch };
			});
			dropFromPending(qc, heldExecutionId);
			if (!batch) refreshSurfaces(qc);
			if (quiet) return;
			const verb = data.state === 'approved' ? 'Approved' : 'Denied';
			toast({
				title: `${verb} execution for ${data.api_vendor}/${data.api_name}`,
				variant: 'success',
			});
		},
		onError: (error, { heldExecutionId }) => {
			if (isAlreadySettled(error)) {
				// Settled by someone else: it leaves every list all the same.
				dropFromPending(qc, heldExecutionId);
				if (!batch) {
					void qc.invalidateQueries({ queryKey: approvalsKeys.all });
					void qc.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot });
				}
			}
			// The review pop-up explains a refused decision in place.
			if (quiet || isDecideForbidden(error)) return;
			toast({
				title: error.message ?? 'Failed to submit decision',
				variant: 'error',
			});
		},
	});
}
