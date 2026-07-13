import { useState } from 'react';
import { ShieldQuestion, CheckCircle2 } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import {
	Card,
	CardHeader,
	CardBody,
	CardTitle,
	Badge,
	EmptyState,
	ErrorAlert,
	SkeletonRows,
	AppLink,
	ActorLabel,
	AgentBadge,
} from '@/shared/ui';
import { AccessRequestDialog } from '@/shared/app';
import {
	usePendingAccessRequests,
	formatApproxCount,
	dashboardKeys,
	type AccessRequest,
} from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
import { timeAgo } from '@/shared/lib/utils';
import { CardRow, CardHeaderIcon } from '@/modules/dashboard/components/CardRow';

/** A decision here must refresh the dashboard surfaces AND the durable
 * access-request queue + nav badge. Both roots come from this module's own
 * factory (the access-request root is re-exposed there from the shared registry
 * so views don't reach into `@/shared/api` directly). */
const ACCESS_REQUESTS_ROOT_KEY = dashboardKeys.accessRequestsRoot;

/**
 * Pending access requests — the DURABLE approval queue.
 *
 * Composed from `GET /access-requests?status=pending`. Unlike the Agent Rail
 * (which only reacts to transient `access_request.filed` SSE events and can
 * miss requests filed outside its stream window), this card reads the requests
 * themselves, so anything still awaiting a human shows up regardless of event
 * timing. Each row opens the shared `AccessRequestDialog` to decide it in place;
 * a successful decision invalidates the queue so the row drops off.
 */
export function PendingAccessRequestsCard() {
	const { data, isLoading, isError, error } = usePendingAccessRequests();
	const queryClient = useQueryClient();
	const [active, setActive] = useState<AccessRequest | null>(null);

	function summarize(request: AccessRequest): string {
		const n = request.items.length;
		const head = request.items[0];
		const label = head ? `${head.resource_type} · ${head.action}` : 'access';
		return n > 1 ? `${label} +${n - 1} more` : label;
	}

	return (
		<>
			<Card className="flex h-full flex-col">
				<CardHeader className="flex items-center justify-between gap-3">
					<CardTitle as="h2" className="flex items-center gap-2.5">
						<CardHeaderIcon>
							<ShieldQuestion className="h-4 w-4" aria-hidden="true" />
						</CardHeaderIcon>
						Access requests awaiting review
					</CardTitle>
					<div className="flex items-center gap-3">
						{data && data.count.value > 0 && (
							<Badge variant="pending" dot>
								{formatApproxCount(data.count)}
							</Badge>
						)}
						<AppLink
							href={ROUTES.accessRequests}
							className="text-primary text-sm font-medium hover:underline"
						>
							View all
						</AppLink>
					</div>
				</CardHeader>
				<CardBody className="flex-1 px-0 py-2">
					{isLoading ? (
						<div className="px-5 py-2">
							<SkeletonRows rows={4} />
						</div>
					) : isError ? (
						<div className="px-5 py-2">
							<ErrorAlert message={error ?? 'Failed to load access requests.'} />
						</div>
					) : !data || data.requests.length === 0 ? (
						<div className="px-5 py-2">
							<EmptyState
								icon={<CheckCircle2 className="h-7 w-7" aria-hidden="true" />}
								title="No requests waiting"
								description="Access requests filed by agents that need a decision will appear here."
							/>
						</div>
					) : (
						<ul className="divide-border/70 divide-y">
							{data.requests.slice(0, 5).map((request) => (
								<li key={request.id}>
									<CardRow
										onClick={() => setActive(request)}
										aria-label={`Review access request ${summarize(request)}`}
										leading={
											<AgentBadge
												id={request.actor_id}
												kind="Agent"
												size="sm"
											/>
										}
										title={summarize(request)}
										subtitle={
											<>
												by <ActorLabel actorId={request.actor_id} />
											</>
										}
										meta={
											request.filed_at ? (
												<time
													dateTime={request.filed_at}
													title={new Date(
														request.filed_at,
													).toLocaleString()}
												>
													{timeAgo(request.filed_at)}
												</time>
											) : undefined
										}
									/>
								</li>
							))}
						</ul>
					)}
				</CardBody>
			</Card>

			<AccessRequestDialog
				open={active !== null}
				requestId={active?.id ?? null}
				onClose={() => setActive(null)}
				onDecided={() => {
					queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
					queryClient.invalidateQueries({ queryKey: ACCESS_REQUESTS_ROOT_KEY });
				}}
			/>
		</>
	);
}
