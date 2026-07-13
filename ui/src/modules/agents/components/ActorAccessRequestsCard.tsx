/**
 * ActorAccessRequestsCard — the access requests THIS actor has filed (#619).
 *
 * Access requests are surfaced org-wide elsewhere (dashboard queue, the rail's
 * live feed, the nav badge), but never scoped to a single actor. The backend
 * keys every request by `actor_id` (the filer's identity — for an agent or
 * service account, that's its own id) and `GET /access-requests` already filters
 * by it, so this card is a thin per-actor read with no new backend surface.
 *
 * Defaults to the still-pending queue — the actionable view, where each row
 * opens the shared `AccessRequestDialog` to decide the request in place. A
 * status filter (mirroring the org-wide queue page) lets an operator pull up the
 * actor's decided history (approved / denied / all) on demand; decided requests
 * render the dialog read-only, so the same surface answers both "what's this
 * agent waiting on?" and "why doesn't it have access X?".
 *
 * A successful decision invalidates this card's slice (so the row leaves the
 * pending view) and the nav-badge count.
 */
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
	LoadingState,
	SegmentedToggle,
	type BadgeVariant,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { AccessRequestDialog } from '@/shared/app';
import {
	useActorAccessRequests,
	actorAccessRequestsRootKey,
	type AccessRequest,
} from '@/modules/agents/api';
import { pendingAccessRequestCountKey } from '@/shared/hooks';
import { timeAgo } from '@/shared/lib/utils';

type StatusFilter = 'pending' | 'approved' | 'denied' | 'all';

const STATUS_OPTIONS: SegmentedToggleOption<StatusFilter>[] = [
	{ value: 'pending', label: 'Pending' },
	{ value: 'approved', label: 'Approved' },
	{ value: 'denied', label: 'Denied' },
	{ value: 'all', label: 'All' },
];

const STATUS_VARIANT: Record<string, BadgeVariant> = {
	pending: 'pending',
	approved: 'success',
	denied: 'danger',
};

/** A one-line summary of a request's items: "toolkit · use +2 more". */
function summarize(request: AccessRequest): string {
	const n = request.items.length;
	const head = request.items[0];
	const label = head ? `${head.resource_type} · ${head.action}` : 'access';
	return n > 1 ? `${label} +${n - 1} more` : label;
}

export function ActorAccessRequestsCard({
	actorId,
	actorName,
}: {
	actorId: string;
	actorName: string;
}) {
	const [status, setStatus] = useState<StatusFilter>('pending');
	const queryStatus = status === 'all' ? null : status;
	const { data, isPending, isError, error } = useActorAccessRequests(actorId, queryStatus);
	const queryClient = useQueryClient();
	const [active, setActive] = useState<AccessRequest | null>(null);

	return (
		<>
			<Card>
				<CardHeader className="flex flex-row items-center justify-between gap-2">
					<CardTitle as="h2" className="flex items-center gap-2">
						<ShieldQuestion
							className="text-warning h-4 w-4 shrink-0"
							aria-hidden="true"
						/>
						Access requests
					</CardTitle>
					{data && data.length > 0 && (
						<Badge variant={STATUS_VARIANT[status] ?? 'default'}>{data.length}</Badge>
					)}
				</CardHeader>
				<CardBody className="space-y-3">
					<SegmentedToggle options={STATUS_OPTIONS} value={status} onChange={setStatus} />

					{isPending ? (
						<LoadingState size="sm" />
					) : isError ? (
						<ErrorAlert
							message={
								error instanceof Error
									? error.message
									: "Failed to load this actor's access requests."
							}
						/>
					) : !data || data.length === 0 ? (
						<EmptyState
							icon={<CheckCircle2 className="h-8 w-8" aria-hidden="true" />}
							title={
								status === 'pending'
									? 'No pending access requests'
									: 'No access requests'
							}
							description={
								status === 'pending'
									? `${actorName} has no access requests awaiting a decision.`
									: `${actorName} has no access requests matching this filter.`
							}
						/>
					) : (
						<ul className="divide-border divide-y">
							{data.map((request) => (
								<li key={request.id}>
									<button
										type="button"
										onClick={() => setActive(request)}
										className="hover:bg-muted/40 flex w-full items-center justify-between gap-3 rounded-md py-2.5 text-left transition-colors"
									>
										<div className="min-w-0">
											<p className="text-foreground truncate font-medium">
												{summarize(request)}
											</p>
											{request.reason && (
												<p className="text-muted-foreground truncate text-xs">
													{request.reason}
												</p>
											)}
										</div>
										<div className="flex shrink-0 items-center gap-3">
											{request.filed_at && (
												<time
													dateTime={request.filed_at}
													title={new Date(
														request.filed_at,
													).toLocaleString()}
													className="text-muted-foreground hidden text-xs whitespace-nowrap tabular-nums sm:inline"
												>
													{timeAgo(request.filed_at)}
												</time>
											)}
											{status === 'all' && (
												<Badge
													variant={
														STATUS_VARIANT[request.status] ?? 'default'
													}
												>
													{request.status}
												</Badge>
											)}
											<span className="text-accent-teal text-sm font-medium">
												{request.status === 'pending' ? 'Review' : 'View'}
											</span>
										</div>
									</button>
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
					// A decision moves a request between the pending / approved /
					// denied / all views, so invalidate the actor's whole by-actor
					// slice (the key root owns the shape — no hand-written literal).
					queryClient.invalidateQueries({
						queryKey: actorAccessRequestsRootKey(actorId),
					});
					queryClient.invalidateQueries({ queryKey: pendingAccessRequestCountKey });
				}}
			/>
		</>
	);
}
