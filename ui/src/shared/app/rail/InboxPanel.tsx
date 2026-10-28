/**
 * InboxPanel — the rail's Inbox tab: every held call waiting on the viewer,
 * across every agent, grouped by agent. The agent the page is about leads;
 * the rest follow the one waiting longest. Each row opens that call's review
 * pop-up (`?review=`), and a group's "Review all" opens its oldest call. Either
 * way the pop-up walks that agent's calls alone, moving on through them as each
 * is decided, and closes after the last.
 *
 * Reads the shared pending list (`usePendingApprovals`), so the rows, the tab's
 * count, the strip's count and the bell always agree. Shared verbatim by the
 * docked rail and the below-`xl` drawer.
 */
import { useMemo, useState } from 'react';
import { ChevronRight, ShieldQuestion } from 'lucide-react';
import { AgentBadge, Button, EmptyState, ErrorAlert, MethodBadge, SkeletonRows } from '@/shared/ui';
import { useRouteAgentId } from '@/shared/app/rail/useRouteAgentId';
import { useNow } from '@/shared/app/rail/useNow';
import {
	groupApprovalsByAgent,
	useCanDecideApprovals,
	useHeldCallHeadlines,
	usePendingApprovals,
	useReviewLinks,
	type AgentPendingApprovals,
	type HeldCall,
} from '@/shared/approvals';
import { waitingSince } from '@/shared/approvals/heldCallCopy';
import { useActorDirectory } from '@/shared/hooks/useActorDirectory';

/** Rows a group shows before "Show all": the rail stays a list, not a wall. */
const GROUP_ROWS = 20;

export type InboxPanelProps = {
	/** Fired after a row opens a review (the drawer closes itself). */
	onNavigated?: () => void;
};

export function InboxPanel({ onNavigated }: InboxPanelProps) {
	const canDecide = useCanDecideApprovals();
	const pending = usePendingApprovals({ enabled: canDecide });
	const calls = useMemo<HeldCall[]>(() => pending.data?.approvals ?? [], [pending.data]);
	const routeAgent = useRouteAgentId();
	const groups = useMemo(() => {
		const byAge = groupApprovalsByAgent(calls);
		// The agent on screen first, then the rest as they were.
		return routeAgent
			? [
					...byAge.filter((g) => g.agentId === routeAgent),
					...byAge.filter((g) => g.agentId !== routeAgent),
				]
			: byAge;
	}, [calls, routeAgent]);
	const directory = useActorDirectory(groups.map((g) => g.agentId));
	const headlineOf = useHeldCallHeadlines(calls);
	const { openReview } = useReviewLinks();

	function review(heldId: string) {
		openReview(heldId);
		onNavigated?.();
	}

	if (!canDecide || (pending.isLoading && !pending.data)) {
		return (
			<div className="px-3 py-3">
				<SkeletonRows rows={3} />
			</div>
		);
	}

	if (pending.isError && !pending.data) {
		return (
			<div className="px-3 py-3">
				<ErrorAlert
					title="Couldn't load the inbox"
					message={
						pending.error instanceof Error ? pending.error : 'Something went wrong.'
					}
					onRetry={() => void pending.refetch()}
					retrying={pending.isFetching}
				/>
			</div>
		);
	}

	if (groups.length === 0) {
		return (
			<EmptyState
				className="bg-transparent px-5 py-10 sm:px-5 sm:py-10"
				icon={<ShieldQuestion className="text-success h-5 w-5" aria-hidden="true" />}
				title="Nothing waiting for you"
				description="Requests show up here the moment an agent asks."
			/>
		);
	}

	return (
		<div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
			{groups.map((group) => (
				<InboxGroup
					key={group.agentId}
					group={group}
					name={directory.resolve(group.agentId) ?? group.agentId}
					headlineOf={headlineOf}
					onReview={review}
				/>
			))}
		</div>
	);
}

function InboxGroup({
	group,
	name,
	headlineOf,
	onReview,
}: {
	group: AgentPendingApprovals;
	name: string;
	headlineOf: (call: HeldCall) => string;
	onReview: (heldId: string) => void;
}) {
	const count = group.approvals.length;
	const [showAll, setShowAll] = useState(false);
	const rows = showAll ? group.approvals : group.approvals.slice(0, GROUP_ROWS);
	return (
		<section aria-label={name} className="pt-2">
			<div className="flex items-center gap-2 px-2 pb-1">
				<AgentBadge id={group.agentId} name={name} size="xs" shape="circle" />
				<span className="text-foreground-name min-w-0 flex-1 truncate text-[12.5px] font-semibold">
					{name}
				</span>
				<span className="text-foreground-faint text-[11px] tabular-nums">
					{count}
					<span className="sr-only"> waiting</span>
				</span>
				<Button
					variant="ghost"
					size="xs"
					className="h-6 px-2 text-[11px]"
					onClick={() => onReview(group.approvals[0].id)}
				>
					Review all
					<span className="sr-only"> from {name}</span>
				</Button>
			</div>
			<ul>
				{rows.map((call) => (
					<InboxRow
						key={call.id}
						call={call}
						headline={headlineOf(call)}
						onReview={onReview}
					/>
				))}
			</ul>
			{rows.length < count && (
				<Button
					variant="ghost"
					size="xs"
					className="mx-2 mt-1 h-6 px-2 text-[11px]"
					onClick={() => setShowAll(true)}
				>
					Show all {count}
					<span className="sr-only"> from {name}</span>
				</Button>
			)}
		</section>
	);
}

function InboxRow({
	call,
	headline,
	onReview,
}: {
	call: HeldCall;
	headline: string;
	onReview: (heldId: string) => void;
}) {
	const now = useNow();
	return (
		<li>
			<button
				type="button"
				data-held-id={call.id}
				onClick={() => onReview(call.id)}
				className="hover:bg-surface-1-hover focus-visible:ring-ring flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-left focus-visible:ring-2 focus-visible:outline-none"
			>
				<MethodBadge method={call.method} className="w-[46px]" />
				<span className="min-w-0 flex-1">
					<span className="text-foreground-name block truncate text-[12.5px] leading-[18px] font-semibold">
						{headline}
					</span>
					<span className="text-foreground-faint block truncate text-[11px] leading-4">
						{call.api_vendor} · {waitingSince(call.created_at, now)}
					</span>
				</span>
				<ChevronRight
					className="text-foreground-faint h-3.5 w-3.5 shrink-0"
					aria-hidden="true"
				/>
			</button>
		</li>
	);
}
