/**
 * Audit tab — the actor lens.
 *
 * Read-only, org:admin-only view over `GET /audit`. This is Monitor's "who did
 * what" surface: every audited action carries `actor_id`/`actor_type` plus the
 * `trace_id`/`job_id` it touched, so the audit log is where execution/job actor
 * attribution actually lives (the wire payloads for those don't carry an actor).
 *
 * Deep-link aware: the trace/job detail sheets link here with
 * `?tab=audit&trace_id=…` or `&target_id=…`; this tab reads those params,
 * applies them as filters, and renders each row's `trace_id`/`job_id` as a
 * deep-link BACK into the Executions/Jobs tabs — closing the cross-reference
 * loop in both directions.
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowUpRight, ShieldAlert, ShieldX, User } from 'lucide-react';
import { AppLink, Badge, EmptyState, ErrorAlert, RefreshButton, ActorLabel } from '@/shared/ui';
import {
	useAudit,
	MonitorApiError,
	AuditTargetType,
	type AuditResponse,
	type ListAuditParams,
} from '@/modules/monitor/api';
import { formatRelative } from '@/modules/monitor/lib/format';
import { monitorHref, hasTrace } from '@/modules/monitor/lib/links';
import { usePermission, ORG_ADMIN } from '@/modules/monitor/lib/usePermission';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import { MonitorList, MonitorRow } from '@/modules/monitor/components/MonitorList';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';

export function AuditTab() {
	const isAdmin = usePermission(ORG_ADMIN);
	const [searchParams] = useSearchParams();
	const filters = useMonitorFilters();

	const traceId = searchParams.get('trace_id');
	const targetId = searchParams.get('target_id');
	// The backend rejects a target_id without its matching target_type, so we
	// only apply the target filter when both arrive together and the type is a
	// value the API recognises.
	const targetTypeParam = searchParams.get('target_type');
	const targetType =
		targetTypeParam && (Object.values(AuditTargetType) as string[]).includes(targetTypeParam)
			? (targetTypeParam as AuditTargetType)
			: null;
	const hasTargetFilter = targetId != null && targetType != null;
	// Actor comes from the global filter bar (?actor_id); the audit endpoint
	// filters by it server-side. The time window maps to `since`.
	const actorId = filters.actorId;

	// The backend `/audit` filter accepts target_type+target_id/actor_id/since
	// (not trace_id), so trace_id is applied client-side over the returned page.
	const filterKey = JSON.stringify({
		targetType: hasTargetFilter ? targetType : null,
		targetId: hasTargetFilter ? targetId : null,
		actorId,
		since: filters.from,
	});
	const pager = useCursorStack(filterKey);
	const params: ListAuditParams = {
		targetType: hasTargetFilter ? targetType : null,
		targetId: hasTargetFilter ? targetId : null,
		actorId: actorId ?? null,
		since: filters.from,
		cursor: pager.cursor,
	};
	const query = useAudit(isAdmin ? params : {});

	const rows = useMemo(() => {
		const data = query.data?.data ?? [];
		return traceId ? data.filter((r) => r.trace_id === traceId) : data;
	}, [query.data, traceId]);

	if (!isAdmin) {
		return (
			<EmptyState
				icon={<ShieldX className="h-8 w-8" />}
				title="Admin only"
				description="The audit log is restricted to organisation admins."
			/>
		);
	}

	// A 403 from the server (e.g. the permission was revoked mid-session) should
	// read as "you can't see this", not a generic failure.
	const isForbidden = query.error instanceof MonitorApiError && query.error.status === 403;

	const activeFilter = traceId
		? { label: 'trace', value: traceId }
		: hasTargetFilter
			? { label: 'target', value: targetId }
			: actorId
				? { label: 'actor', value: actorId }
				: null;

	const showEmpty = rows.length === 0 && !query.isLoading && !query.isFetching;

	// Trace/job deep-link shared by the desktop column and the mobile card.
	const renderLink = (row: AuditResponse) => {
		if (hasTrace(row.trace_id)) {
			return (
				<AppLink
					href={monitorHref({ tab: 'executions', traceId: row.trace_id })}
					className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
					aria-label={`Open trace ${row.trace_id} in Executions`}
				>
					Trace
					<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
				</AppLink>
			);
		}
		if (row.job_id) {
			return (
				<AppLink
					href={monitorHref({ tab: 'jobs', jobId: row.job_id })}
					className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
					aria-label={`Open job ${row.job_id} in Jobs`}
				>
					Job
					<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
				</AppLink>
			);
		}
		return null;
	};

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-2">
				<div className="flex items-center gap-2">
					{activeFilter ? (
						<Badge variant="default">
							{activeFilter.label}:{' '}
							<span className="font-mono">{activeFilter.value}</span>
						</Badge>
					) : (
						<p className="text-muted-foreground text-sm">All audited actions</p>
					)}
				</div>
				<RefreshButton onRefresh={() => query.refetch()} pending={query.isFetching} />
			</div>

			{isForbidden ? (
				<EmptyState
					icon={<ShieldX className="h-8 w-8" />}
					title="Admin only"
					description="The audit log is restricted to organisation admins."
				/>
			) : query.isError ? (
				<ErrorAlert
					message={
						query.error instanceof Error ? query.error : 'Failed to load the audit log.'
					}
					onRetry={() => query.refetch()}
					retrying={query.isFetching}
				/>
			) : showEmpty ? (
				<EmptyState
					icon={<ShieldAlert className="h-8 w-8" />}
					title={activeFilter ? 'No matching audit entries' : 'No audit entries yet'}
					description={
						activeFilter
							? 'No audited actions match the current filter.'
							: 'Audited actions (who did what) will appear here as your team operates the platform.'
					}
				/>
			) : (
				<MonitorList title="Audit log" ariaLabel="Audit log" isLoading={query.isLoading}>
					{rows.map((row) => (
						<MonitorRow
							key={row.id}
							accent="neutral"
							icon={<User className="h-4 w-4" />}
							title={<span className="font-mono">{row.action}</span>}
							subtitle={
								<>
									{row.actor_id ? (
										<ActorLabel
											actorId={row.actor_id}
											actorType={row.actor_type}
											className="text-foreground font-medium"
										/>
									) : (
										<span className="text-foreground font-medium">
											{row.actor_type}
										</span>
									)}
									<span>
										{' '}
										→ {row.target_type} · {row.target_id}
									</span>
								</>
							}
							meta={
								<>
									<span>{formatRelative(row.occurred_at)}</span>
									{renderLink(row)}
								</>
							}
						/>
					))}
				</MonitorList>
			)}

			{traceId && !showEmpty && (
				<p className="text-muted-foreground text-xs">
					Filtering the visible page by trace <span className="font-mono">{traceId}</span>{' '}
					(the audit API has no trace filter, so paging steps through unfiltered pages).
				</p>
			)}

			{!isForbidden && !query.isError && !showEmpty && !traceId && (
				<CursorPager
					hasMore={query.data?.has_more ?? false}
					hasPrev={pager.hasPrev}
					onOlder={() => pager.pushNext(query.data?.next_cursor)}
					onNewer={pager.goPrev}
					page={pager.page}
					loading={query.isFetching}
				/>
			)}
		</div>
	);
}
