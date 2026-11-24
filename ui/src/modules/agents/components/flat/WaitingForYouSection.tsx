/**
 * WaitingForYouSection — the Agents page's "Waiting for you" callout: agents
 * blocked until a human acts on a connect request — the agent asked to connect
 * an account (`jentic connect`, MCP `request_connection`). Its Review links
 * are the token-less `?approve=<sid>` address the backend relays to the agent,
 * plus `?agent=` so the requesting agent is selected behind the dialog.
 *
 * That link may never reach anyone, so the page reads the live state (open
 * connect sessions) instead of relying on it. One row per agent (oldest
 * first), with a Review link per request. Nothing waiting renders NOTHING; a
 * caller who cannot approve (see `useCanApproveConnectRequests`) makes no
 * request.
 */
import { useMemo, type ReactNode } from 'react';
import { AppLink, Tooltip, UserText } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { useActorDirectory } from '@/shared/hooks';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import { ATTENTION_ROW_CLASS } from '@/shared/approvals';
import { useOpenConnectRequests } from '@/shared/credentials/api';
import {
	groupConnectRequestsByAgent,
	describeConnectAsks,
	useCanApproveConnectRequests,
	type AgentConnectRequests,
} from '@/shared/credentials/lib/connectRequests';
import { waitingLabel } from '@/modules/agents/components/flat/PendingApprovalBanner';

export function WaitingForYouSection() {
	const canApprove = useCanApproveConnectRequests();
	const requests = useOpenConnectRequests({ enabled: canApprove });
	const connectGroups = useMemo(
		() => groupConnectRequestsByAgent(requests.data ?? []),
		[requests.data],
	);
	const directory = useActorDirectory(connectGroups.map((g) => g.agentId));

	// The inbox reads the connect-request query for every credentials reader,
	// so the cache can hold requests this viewer may not approve: gate the
	// render too.
	const shownConnect = canApprove ? connectGroups : [];
	if (shownConnect.length === 0) return null;

	return (
		<section role="region" aria-labelledby="waiting-for-you-heading" className="space-y-1.5">
			<h2
				id="waiting-for-you-heading"
				className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase"
			>
				Waiting for you
			</h2>
			<ul className="space-y-1.5">
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
		<li
			className={cn(
				ATTENTION_ROW_CLASS,
				'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg px-3.5 py-2.5',
			)}
		>
			<span className="bg-warning h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden="true" />
			<p className="min-w-0 flex-1 basis-52 text-sm">
				<UserText className="font-heading font-semibold">{name}</UserText>{' '}
				<span className="text-muted-foreground">
					{what}
					{' · '}
					<Tooltip content={`Asked ${formatTimestamp(since)}`}>
						{waitingLabel(since)}
					</Tooltip>
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
			what={`wants to ${describeConnectAsks(group.sessions)}`}
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
								{name}&apos;s request to{' '}
								{session.intent === 'edit'
									? `change its access to ${session.vendor_display_name}`
									: `connect ${session.vendor_display_name}`}
							</span>
						</>
					)}
				</AppLink>
			))}
		</WaitingRow>
	);
}
