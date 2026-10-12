/**
 * ConnectRequestsSection — the Agents page's "Waiting for you" callout. An agent
 * that asked to connect an account (`jentic connect`, MCP `request_connection`)
 * is blocked until a human approves; its approval link may never reach anyone,
 * so the page reads the live open sessions instead of relying on the link.
 *
 * One row per agent (oldest request first), naming what it wants to connect,
 * with a Review link per request that opens it for approval. The links are the
 * token-less `?approve=<sid>` address the backend relays to the agent, plus
 * `?agent=` so the requesting agent is selected behind the dialog.
 * Nothing open renders NOTHING; a caller who cannot approve requests (see
 * `useCanApproveConnectRequests`) makes no request.
 */
import { useMemo } from 'react';
import { AppLink, Tooltip, UserText } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { useActorDirectory } from '@/shared/hooks';
import { formatTimestamp } from '@/shared/lib/utils';
import { useOpenConnectRequests } from '@/shared/credentials/api';
import {
	groupConnectRequestsByAgent,
	summariseConnectTargets,
	useCanApproveConnectRequests,
} from '@/shared/credentials/lib/connectRequests';
import { waitingLabel } from '@/modules/agents/components/flat/PendingApprovalBanner';

export function ConnectRequestsSection() {
	const canApprove = useCanApproveConnectRequests();
	const requests = useOpenConnectRequests({ enabled: canApprove });
	const groups = useMemo(() => groupConnectRequestsByAgent(requests.data ?? []), [requests.data]);
	const directory = useActorDirectory(groups.map((g) => g.agentId));

	// The inbox reads the same query for every credentials reader, so the
	// cache can hold requests this viewer may not approve: gate the render too.
	if (!canApprove || groups.length === 0) return null;

	return (
		<section role="region" aria-labelledby="connect-requests-heading" className="space-y-1.5">
			<h2
				id="connect-requests-heading"
				className="text-foreground-faint text-[10.5px] font-bold tracking-[0.08em] uppercase"
			>
				Waiting for you
			</h2>
			<ul className="space-y-1.5">
				{groups.map((group) => {
					const name = directory.resolve(group.agentId) ?? group.agentId;
					return (
						<li
							key={group.agentId}
							className="bg-warning/10 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg px-3.5 py-2.5"
						>
							<span
								className="bg-warning h-1.5 w-1.5 shrink-0 rounded-full"
								aria-hidden="true"
							/>
							<p className="min-w-0 flex-1 basis-52 text-sm">
								<UserText className="font-heading font-semibold">{name}</UserText>{' '}
								<span className="text-muted-foreground">
									wants to connect {summariseConnectTargets(group.sessions)}
									{' · '}
									<Tooltip content={`Asked ${formatTimestamp(group.since)}`}>
										{waitingLabel(group.since)}
									</Tooltip>
								</span>
							</p>
							<span className="flex flex-wrap items-center gap-2">
								{group.sessions.map((session) => (
									<AppLink
										key={session.session_id}
										href={ROUTE_PATHS.connectApproval(
											session.session_id,
											group.agentId,
										)}
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
													{name}&apos;s request to connect{' '}
													{session.vendor_display_name}
												</span>
											</>
										)}
									</AppLink>
								))}
							</span>
						</li>
					);
				})}
			</ul>
		</section>
	);
}
