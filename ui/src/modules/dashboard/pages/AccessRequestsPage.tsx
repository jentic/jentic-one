import { useMemo, useState } from 'react';
import { CheckCircle2, ChevronRight } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import {
	PageShell,
	PageHeader,
	PageHelp,
	BackButton,
	Button,
	Badge,
	Card,
	CardBody,
	EmptyState,
	ErrorAlert,
	SkeletonRows,
	SegmentedToggle,
	ActorLabel,
	AgentBadge,
	type BadgeVariant,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { AccessRequestDialog } from '@/shared/app';
import { useAccessRequestsQueue, dashboardKeys, type AccessRequest } from '@/modules/dashboard/api';
import { ROUTES } from '@/shared/app/routes';
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

/** A decision moves a row between status filters and changes the dashboard
 * surfaces + nav badge. Both roots come from this module's own factory (the
 * access-request root is re-exposed there from the shared registry so views
 * don't reach into `@/shared/api` directly). */
const ACCESS_REQUESTS_ROOT_KEY = dashboardKeys.accessRequestsRoot;

function summarize(request: AccessRequest): string {
	const n = request.items.length;
	const head = request.items[0];
	const label = head ? `${head.resource_type} · ${head.action}` : 'access';
	return n > 1 ? `${label} +${n - 1} more` : label;
}

/**
 * Full access-request queue (`ROUTES.accessRequests`) — the "View all" target of
 * the Dashboard's Pending requests card. Reads the DURABLE queue
 * (`GET /access-requests`) cursor-paginated, filterable by status, with each row
 * opening the shared AccessRequestDialog to decide it in place. A successful
 * decision invalidates the queue + the nav badge so the row reflects its new
 * state immediately.
 */
export default function AccessRequestsPage() {
	const [status, setStatus] = useState<StatusFilter>('pending');
	const [active, setActive] = useState<AccessRequest | null>(null);
	const queryClient = useQueryClient();

	const { data, isLoading, isError, error, hasNextPage, isFetchingNextPage, fetchNextPage } =
		useAccessRequestsQueue(status === 'all' ? '' : status);

	const requests = useMemo(() => (data?.pages ?? []).flatMap((page) => page.data), [data]);

	function onDecided() {
		queryClient.invalidateQueries({ queryKey: dashboardKeys.all });
		queryClient.invalidateQueries({ queryKey: ACCESS_REQUESTS_ROOT_KEY });
	}

	return (
		<PageShell>
			<PageHeader
				title="Access requests"
				subtitle="The full queue of access requests filed by agents."
				actions={
					<PageHelp
						title="About access requests"
						intro="When an agent needs a permission it does not yet hold, it files an access request that waits here for a human decision."
						sections={[
							{
								heading: 'Deciding a request',
								body: 'Open a request to approve or deny each item individually. A denial carries a reason that is fed back to the agent.',
							},
							{
								heading: 'Durable, unlike the rail',
								body: 'The Agent Rail only flashes requests as they are filed. This page reads the requests themselves, so anything still awaiting a decision shows up regardless of event timing.',
							},
						]}
					/>
				}
			/>
			<BackButton to={ROUTES.app} label="Back to Dashboard" />

			<SegmentedToggle options={STATUS_OPTIONS} value={status} onChange={setStatus} />

			<Card>
				<CardBody className="px-0 py-0">
					{isLoading ? (
						<div className="px-5 py-4">
							<SkeletonRows rows={6} />
						</div>
					) : isError ? (
						<div className="p-5">
							<ErrorAlert
								message={error?.message ?? 'Failed to load access requests.'}
							/>
						</div>
					) : requests.length === 0 ? (
						<div className="p-5">
							<EmptyState
								icon={<CheckCircle2 className="h-7 w-7" aria-hidden="true" />}
								title="No requests"
								description={
									status === 'pending'
										? 'Nothing is awaiting a decision right now.'
										: 'No access requests match this filter.'
								}
							/>
						</div>
					) : (
						<ul className="divide-border/70 divide-y">
							{requests.map((request) => (
								<li key={request.id}>
									<button
										type="button"
										onClick={() => setActive(request)}
										className="group hover:bg-muted/40 flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors"
									>
										<div className="flex min-w-0 items-center gap-3">
											<AgentBadge
												id={request.actor_id}
												kind="Agent"
												size="sm"
											/>
											<div className="min-w-0">
												<p className="text-foreground truncate font-medium">
													{summarize(request)}
												</p>
												<p className="text-muted-foreground truncate text-xs">
													by <ActorLabel actorId={request.actor_id} />
												</p>
											</div>
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
											<Badge
												variant={
													STATUS_VARIANT[request.status] ?? 'default'
												}
												dot
											>
												{request.status}
											</Badge>
											<span className="text-muted-foreground group-hover:text-primary flex items-center gap-0.5 text-sm font-medium transition-colors">
												{request.status === 'pending' ? 'Review' : 'View'}
												<ChevronRight
													className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
													aria-hidden="true"
												/>
											</span>
										</div>
									</button>
								</li>
							))}
						</ul>
					)}
				</CardBody>
			</Card>

			{hasNextPage && (
				<div className="flex justify-center">
					<Button
						variant="secondary"
						size="sm"
						onClick={() => fetchNextPage()}
						disabled={isFetchingNextPage}
					>
						{isFetchingNextPage ? 'Loading…' : 'Load more'}
					</Button>
				</div>
			)}

			<AccessRequestDialog
				open={active !== null}
				requestId={active?.id ?? null}
				onClose={() => setActive(null)}
				onDecided={onDecided}
			/>
		</PageShell>
	);
}
