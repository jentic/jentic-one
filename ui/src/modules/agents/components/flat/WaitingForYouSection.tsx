/**
 * WaitingForYouSection — the Agents page's "Waiting for you" callout: agents
 * blocked until a human acts. Two kinds of wait share it:
 *
 *   - a connect request — the agent asked to connect an account (`jentic
 *     connect`, MCP `request_connection`). Its Review links are the token-less
 *     `?approve=<sid>` address the backend relays to the agent, plus `?agent=`
 *     so the requesting agent is selected behind the dialog.
 *   - a held call — the agent's call matched an Ask rule and waits on its
 *     owner (or an org admin) to approve or deny it. Its Review links open the
 *     approval's review page, the address the broker returns as `review_url`.
 *
 * Either link may never reach anyone, so the page reads the live state
 * (open connect sessions, pending approvals) instead of relying on it. One row
 * per agent and kind (oldest first), with a Review link per request or call.
 * Nothing waiting renders NOTHING; a caller who cannot act on a kind (see
 * `useCanApproveConnectRequests`, `useCanDecideApprovals`) makes no request
 * for it.
 */
import { useMemo, type ReactNode } from 'react';
import { AppLink } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { useActorDirectory } from '@/shared/hooks';
import { formatTimestamp } from '@/shared/lib/utils';
import { useOpenConnectRequests } from '@/shared/credentials/api';
import {
	groupConnectRequestsByAgent,
	summariseConnectTargets,
	useCanApproveConnectRequests,
	type AgentConnectRequests,
} from '@/shared/credentials/lib/connectRequests';
import {
	describeHeldCall,
	groupApprovalsByAgent,
	summariseHeldCalls,
	useCanDecideApprovals,
	usePendingApprovals,
	type AgentPendingApprovals,
} from '@/shared/approvals';
import { waitingLabel } from '@/modules/agents/components/flat/PendingApprovalBanner';

export function WaitingForYouSection() {
	const canApprove = useCanApproveConnectRequests();
	const requests = useOpenConnectRequests({ enabled: canApprove });
	const connectGroups = useMemo(
		() => groupConnectRequestsByAgent(requests.data ?? []),
		[requests.data],
	);
	const canDecide = useCanDecideApprovals();
	const approvals = usePendingApprovals({ enabled: canDecide });
	const approvalGroups = useMemo(
		() => groupApprovalsByAgent(approvals.data?.approvals ?? []),
		[approvals.data],
	);
	const directory = useActorDirectory([
		...connectGroups.map((g) => g.agentId),
		...approvalGroups.map((g) => g.agentId),
	]);

	// The inbox reads the connect-request query for every credentials reader,
	// so the cache can hold requests this viewer may not approve: gate the
	// render too. Likewise for held calls.
	const shownConnect = canApprove ? connectGroups : [];
	const shownApprovals = canDecide ? approvalGroups : [];
	if (shownConnect.length === 0 && shownApprovals.length === 0) return null;

	return (
		<section role="region" aria-labelledby="waiting-for-you-heading" className="space-y-1.5">
			<h2
				id="waiting-for-you-heading"
				className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase"
			>
				Waiting for you
			</h2>
			<ul className="space-y-1.5">
				{shownApprovals.map((group) => (
					<HeldCallsRow
						key={`approval:${group.agentId}`}
						group={group}
						name={directory.resolve(group.agentId) ?? group.agentId}
					/>
				))}
				{shownConnect.map((group) => (
					<ConnectRequestsRow
						key={`connect:${group.agentId}`}
						group={group}
						name={directory.resolve(group.agentId) ?? group.agentId}
					/>
				))}
			</ul>
		</section>
	);
}

function WaitingRow({
	name,
	what,
	since,
	children,
}: {
	name: string;
	what: string;
	since: string;
	children: ReactNode;
}) {
	return (
		<li className="bg-warning/10 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg px-3.5 py-2.5">
			<span className="bg-warning h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden="true" />
			<p className="min-w-0 flex-1 basis-52 text-sm">
				<span className="font-heading font-semibold">{name}</span>{' '}
				<span className="text-muted-foreground">
					{what}
					{' · '}
					<span title={`Asked ${formatTimestamp(since)}`}>{waitingLabel(since)}</span>
				</span>
			</p>
			<span className="flex flex-wrap items-center gap-2">{children}</span>
		</li>
	);
}

function ConnectRequestsRow({ group, name }: { group: AgentConnectRequests; name: string }) {
	return (
		<WaitingRow
			name={name}
			what={`wants to connect ${summariseConnectTargets(group.sessions)}`}
			since={group.since}
		>
			{group.sessions.map((session) => (
				<AppLink
					key={session.session_id}
					href={ROUTE_PATHS.connectApproval(session.session_id, group.agentId)}
					variant="tonal"
					size="sm"
				>
					{/* The accessible name starts with the visible label
					    and says whose request it is. */}
					{group.sessions.length > 1 ? (
						<>
							Review {session.vendor_display_name}
							<span className="sr-only"> for {name}</span>
						</>
					) : (
						<>
							Review
							<span className="sr-only">
								{' '}
								{name}&apos;s request to connect {session.vendor_display_name}
							</span>
						</>
					)}
				</AppLink>
			))}
		</WaitingRow>
	);
}

function HeldCallsRow({ group, name }: { group: AgentPendingApprovals; name: string }) {
	return (
		<WaitingRow
			name={name}
			what={`wants to make ${summariseHeldCalls(group.approvals)}`}
			since={group.since}
		>
			{group.approvals.map((approval) => (
				<AppLink
					key={approval.id}
					href={ROUTE_PATHS.approval(approval.id)}
					variant="tonal"
					size="sm"
					title={describeHeldCall(approval)}
				>
					{/* The accessible name starts with the visible label and
					    names the held call. */}
					{group.approvals.length > 1 ? (
						<>
							Review {approval.method} {approval.path}
							<span className="sr-only"> for {name}</span>
						</>
					) : (
						<>
							Review
							<span className="sr-only">
								{' '}
								{name}&apos;s held call {describeHeldCall(approval)}
							</span>
						</>
					)}
				</AppLink>
			))}
		</WaitingRow>
	);
}
