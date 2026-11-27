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
 *
 * It also lists access ending soon: a binding whose owner asked to be warned
 * will reach its end date, or its idle limit unless the agent uses it, within
 * two days. Review opens the agent, whose API tile renews it.
 */
import { isoDurationSeconds } from '@/shared/lib';
import { useMemo, type ReactNode } from 'react';
import { AGENTS_READ, useCanAccess } from '@/shared/auth';
import { AppLink, Tooltip, UserText } from '@/shared/ui';
import { ROUTE_PATHS } from '@/shared/app';
import { useActorDirectory } from '@/shared/hooks';
import { cn, formatTimestamp } from '@/shared/lib/utils';
import { ATTENTION_ROW_CLASS } from '@/shared/approvals';
import { bindingExpiryLabel } from '@/shared/credentials/lib/bindingExpiry';
import {
	useBindingsEndingSoon,
	useOpenConnectRequests,
	type EndingSoonBindingResponse,
} from '@/shared/credentials/api';
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
	const canReadAgents = useCanAccess(AGENTS_READ);
	const endingSoon = useBindingsEndingSoon({ enabled: canReadAgents });
	const endingGroups = useMemo(() => groupByAgent(endingSoon.data ?? []), [endingSoon.data]);
	const directory = useActorDirectory(connectGroups.map((g) => g.agentId));

	// The inbox reads the connect-request query for every credentials reader,
	// so the cache can hold requests this viewer may not approve: gate the
	// render too.
	const shownConnect = canApprove ? connectGroups : [];
	const shownEnding = canReadAgents ? endingGroups : [];
	if (shownConnect.length === 0 && shownEnding.length === 0) return null;

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
				{shownEnding.map((group) => (
					<EndingSoonRow key={`ending:${group.agentId}`} group={group} />
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
	testId,
}: {
	name: string;
	what: string;
	/** When the wait started; left out, the row shows no "waiting" age. */
	since?: string;
	children: ReactNode;
	testId?: string;
}) {
	return (
		<li
			className={cn(
				ATTENTION_ROW_CLASS,
				'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg px-3.5 py-2.5',
			)}
			data-testid={testId}
		>
			<span className="bg-warning h-1.5 w-1.5 shrink-0 rounded-full" aria-hidden="true" />
			<p className="min-w-0 flex-1 basis-52 text-sm">
				<UserText className="font-heading font-semibold">{name}</UserText>{' '}
				<span className="text-muted-foreground">
					{what}
					{since !== undefined && (
						<>
							{' · '}
							<Tooltip content={`Asked ${formatTimestamp(since)}`}>
								{waitingLabel(since)}
							</Tooltip>
						</>
					)}
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

interface AgentEndingSoon {
	agentId: string;
	agentName: string;
	/** Soonest first, as the server orders them. */
	bindings: EndingSoonBindingResponse[];
}

/** One group per agent, in the order its soonest binding ends. */
function groupByAgent(rows: readonly EndingSoonBindingResponse[]): AgentEndingSoon[] {
	const groups = new Map<string, AgentEndingSoon>();
	for (const row of rows) {
		const group = groups.get(row.agent_id);
		if (group) group.bindings.push(row);
		else
			groups.set(row.agent_id, {
				agentId: row.agent_id,
				agentName: row.agent_name,
				bindings: [row],
			});
	}
	return [...groups.values()];
}

/**
 * When one binding ends, by the limit due a warning: "Expires in 1 day" for
 * its end date, "Ends in 1 day unless used" for its idle limit.
 */
function endsLabel(binding: EndingSoonBindingResponse): string {
	const label =
		binding.ending === 'idle'
			? bindingExpiryLabel({
					expiresAt: null,
					expired: false,
					maxIdlePeriod: isoDurationSeconds(binding.max_idle_period),
					idleExpiresAt: binding.ends_at,
				})
			: bindingExpiryLabel({ expiresAt: binding.ends_at, expired: false });
	return label ?? 'Ends soon';
}

/** "access to Stripe that expires in 1 day", or a count for several. */
function describeEndingSoon(bindings: readonly EndingSoonBindingResponse[]): string {
	if (bindings.length > 1) {
		const unlessUsed = bindings.every((b) => b.ending === 'idle') ? ' unless used' : '';
		return `has access to ${bindings.length} credentials ending soon${unlessUsed}`;
	}
	const [binding] = bindings;
	const label = endsLabel(binding);
	return `has access to ${binding.name ?? 'a credential'} that ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

function EndingSoonRow({ group }: { group: AgentEndingSoon }) {
	const soonest = group.bindings[0];
	return (
		<WaitingRow
			name={group.agentName}
			what={describeEndingSoon(group.bindings)}
			testId="waiting-ending-soon"
		>
			<AppLink
				href={ROUTE_PATHS.agentTab(group.agentId)}
				variant="tonal"
				size="sm"
				title={
					soonest
						? soonest.ending === 'idle'
							? `Ends if unused by ${formatTimestamp(soonest.ends_at)}`
							: `Ends ${formatTimestamp(soonest.ends_at)}`
						: undefined
				}
			>
				Review
				<span className="sr-only"> {group.agentName}&apos;s access that ends soon</span>
			</AppLink>
		</WaitingRow>
	);
}
