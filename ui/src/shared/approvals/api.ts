/**
 * Pending execution approvals — the live state behind the attention inbox's
 * held-call rows, the Agents page "Waiting for you" section and the
 * pending-count badges. Reads `GET /executions/approvals?state=pending`, which
 * the server scopes to the approvals the caller may review (the agent's owner,
 * or `org:admin` for every agent).
 *
 * Shared rather than in the Agents module because the inbox and the nav badge
 * are shell chrome; the Agents module's Approvals pages keep their own list and
 * detail slices under `sharedQueryKeys.approvalsRoot`.
 */
import { useQuery } from '@tanstack/react-query';
import {
	ExecutionApprovalsService,
	ExecutionApprovalState,
	sharedQueryKeys,
	type ExecutionApprovalResponse,
} from '@/shared/api';
import { useCanDecideApprovals } from '@/shared/approvals/pendingApprovals';

/**
 * Under the attention root, so anything that refreshes the inbox (a decision
 * made on the review page, an approval event on the live stream) refreshes the
 * badges and the "Waiting for you" rows with it.
 */
export const pendingApprovalsKey = [
	...sharedQueryKeys.attentionRoot,
	'execution-approvals',
] as const;

/** The list endpoint's largest page. */
const PAGE_LIMIT = 100;

/**
 * Pages read per refresh. The backend caps pending holds per agent, so a few
 * pages cover any realistic fleet; past this the count is a floor.
 */
const MAX_PAGES = 5;

/** Same cadence as the rest of the inbox: roughly live without a push channel. */
const REFETCH_MS = 45_000;

export interface PendingApprovals {
	/** Still decidable (pending and not past `expires_at`), oldest first. */
	approvals: ExecutionApprovalResponse[];
	/** True when more pending approvals exist than were read. */
	truncated: boolean;
}

/**
 * Drop a pending row whose window already lapsed: the expiry sweep records it
 * shortly, and until then nobody can decide it.
 */
function stillDecidable(approval: ExecutionApprovalResponse, now: number): boolean {
	return Date.parse(approval.expires_at) > now;
}

export async function listPendingApprovals(): Promise<PendingApprovals> {
	const rows: ExecutionApprovalResponse[] = [];
	let cursor: string | null = null;
	let truncated = false;
	for (let page = 0; page < MAX_PAGES; page++) {
		const res = await ExecutionApprovalsService.listExecutionApprovals({
			state: ExecutionApprovalState.PENDING,
			limit: PAGE_LIMIT,
			cursor,
		});
		rows.push(...res.data);
		if (!res.has_more || !res.next_cursor) break;
		cursor = res.next_cursor;
		truncated = page === MAX_PAGES - 1;
	}
	const now = Date.now();
	return {
		approvals: rows
			.filter((row) => stillDecidable(row, now))
			.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)),
		truncated,
	};
}

/**
 * The approvals waiting on the caller. Pass `enabled: false` for a caller who
 * cannot decide them (see `useCanDecideApprovals`): no request goes out.
 */
export function usePendingApprovals(options?: { enabled?: boolean }) {
	return useQuery<PendingApprovals>({
		queryKey: pendingApprovalsKey,
		queryFn: listPendingApprovals,
		enabled: options?.enabled ?? true,
		staleTime: 30_000,
		refetchInterval: REFETCH_MS,
	});
}

/**
 * The pending-count badges' read: how many held calls wait on the viewer. Off
 * (zero, no request) for a caller who cannot decide them. Shares the one cache
 * slice with the inbox and "Waiting for you", so every count agrees.
 */
export function usePendingApprovalsCount(): { count: number; atLeast: boolean } {
	const canDecide = useCanDecideApprovals();
	const { data } = usePendingApprovals({ enabled: canDecide });
	if (!canDecide || !data) return { count: 0, atLeast: false };
	return { count: data.approvals.length, atLeast: data.truncated };
}
