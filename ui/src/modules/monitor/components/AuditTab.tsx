/**
 * Audit log — the actor lens, one source of the Activity view.
 *
 * Read-only, org:admin-only view over `GET /audit`: who did what. Each entry
 * reads as a sentence ("Cancelled a job", "Signed in" — see
 * lib/describeAudit) with its actor, target and the reason or change on the
 * second line; the raw action, ids, IP and client are one click away in the
 * detail pane (`?audit_id=`).
 *
 * Deep-link aware: the call/job records link here with
 * `?show=audit&trace_id=…` or `&target_type=…&target_id=…`; this source reads
 * those as filters, and each row's trace/job links BACK into the API calls /
 * Jobs sources — closing the cross-reference loop in both directions.
 */
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { ArrowUpRight, ShieldAlert, ShieldX, X } from 'lucide-react';
import {
	ActorLabel,
	AppLink,
	Badge,
	Button,
	EmptyState,
	ErrorAlert,
	SkeletonRows,
} from '@/shared/ui';
import {
	AuditTargetType,
	MonitorApiError,
	useAudit,
	type AuditResponse,
	type ListAuditParams,
} from '@/modules/monitor/api';
import { CursorPager } from '@/modules/monitor/components/CursorPager';
import { groupByDay, LogDay, LogList, LogRow } from '@/modules/monitor/components/LogList';
import { LogLayout } from '@/modules/monitor/components/LogDetailPane';
import { AuditDetail } from '@/modules/monitor/components/AuditDetail';
import { hasTrace, monitorHref } from '@/modules/monitor/lib/links';
import { originLabel } from '@/modules/monitor/lib/logVocabulary';
import { usePermission, ORG_ADMIN } from '@/modules/monitor/lib/usePermission';
import { useMonitorFilters } from '@/modules/monitor/lib/useMonitorFilters';
import { useCursorStack } from '@/modules/monitor/lib/useCursorStack';
import { useLogDetail } from '@/modules/monitor/lib/useLogDetail';
import {
	auditChanges,
	auditChangeSummary,
	auditSentence,
	auditTargetLabel,
	auditTone,
} from '@/modules/monitor/lib/describeAudit';

const TONE_LABEL = { fail: 'Failed', warn: 'Notable', neutral: 'Recorded' } as const;

/** The trace/job an entry touched, as a link back into its source. */
function CrossLink({ row }: { row: AuditResponse }) {
	const className =
		'text-primary inline-flex items-center gap-0.5 rounded px-1 py-0.5 text-xs font-medium hover:underline';
	if (hasTrace(row.trace_id)) {
		return (
			<AppLink
				href={monitorHref({ show: 'calls', traceId: row.trace_id })}
				className={className}
				aria-label={`Open trace ${row.trace_id} in API calls`}
			>
				Trace
				<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
			</AppLink>
		);
	}
	if (row.job_id) {
		return (
			<AppLink
				href={monitorHref({ show: 'jobs', jobId: row.job_id })}
				className={className}
				aria-label={`Open job ${row.job_id} in Jobs`}
			>
				Job
				<ArrowUpRight className="h-3 w-3" aria-hidden="true" />
			</AppLink>
		);
	}
	return null;
}

export function AuditTab() {
	const isAdmin = usePermission(ORG_ADMIN);
	const [searchParams, setSearchParams] = useSearchParams();
	const filters = useMonitorFilters();
	const { detail, open, close, docked } = useLogDetail('audit');

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
	const actorId = filters.actorId;

	// The backend `/audit` filter accepts target_type+target_id/actor_id/
	// since/until (not trace_id), so trace_id is applied client-side over the
	// returned page.
	const filterKey = JSON.stringify({
		targetType: hasTargetFilter ? targetType : null,
		targetId: hasTargetFilter ? targetId : null,
		actorId,
		since: filters.from,
		until: filters.to,
	});
	const pager = useCursorStack(filterKey);
	const params: ListAuditParams = {
		targetType: hasTargetFilter ? targetType : null,
		targetId: hasTargetFilter ? targetId : null,
		actorId: actorId ?? null,
		since: filters.from,
		until: filters.to,
		cursor: pager.cursor,
	};
	const query = useAudit(params, { enabled: isAdmin });

	const rows = useMemo(() => {
		const data = query.data?.data ?? [];
		return traceId ? data.filter((r) => r.trace_id === traceId) : data;
	}, [query.data, traceId]);
	const days = useMemo(() => groupByDay(rows, (r) => Date.parse(r.occurred_at)), [rows]);

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

	const deepFilter = traceId
		? { label: 'trace', value: traceId }
		: hasTargetFilter
			? { label: 'target', value: targetId }
			: null;
	const narrowed = deepFilter != null || actorId != null || filters.range != null;
	const clearDeepFilter = () => {
		setSearchParams(
			(prev) => {
				const next = new URLSearchParams(prev);
				for (const k of ['trace_id', 'target_id', 'target_type']) next.delete(k);
				return next;
			},
			{ replace: true },
		);
	};

	const showEmpty = rows.length === 0 && !query.isLoading && !query.isFetching;

	return (
		<LogLayout
			detail={detail}
			docked={docked}
			onClose={close}
			renderDetail={(d, frame) => (
				<AuditDetail
					key={d.id}
					auditId={d.id}
					entry={rows.find((r) => r.id === d.id)}
					frame={frame}
				/>
			)}
		>
			<div className="space-y-3">
				{/* A deep-linked trace/target narrows the log; the actor is the
				    toolbar's own filter, so it isn't repeated here. */}
				{deepFilter && (
					<div className="flex items-center gap-2">
						<Badge variant="default">
							{deepFilter.label}:{' '}
							<span className="font-mono">{deepFilter.value}</span>
						</Badge>
						<Button
							variant="ghost"
							size="sm"
							onClick={clearDeepFilter}
							aria-label={`Clear ${deepFilter.label} filter`}
						>
							<X className="h-3.5 w-3.5" aria-hidden="true" />
							Clear
						</Button>
					</div>
				)}

				{isForbidden ? (
					<EmptyState
						icon={<ShieldX className="h-8 w-8" />}
						title="Admin only"
						description="The audit log is restricted to organisation admins."
					/>
				) : query.isError ? (
					<ErrorAlert
						message={
							query.error instanceof Error
								? query.error
								: 'Failed to load the audit log.'
						}
						onRetry={() => query.refetch()}
						retrying={query.isFetching}
					/>
				) : showEmpty ? (
					<EmptyState
						icon={<ShieldAlert className="h-8 w-8" />}
						title={narrowed ? 'No matching audit entries' : 'No audit entries yet'}
						description={
							narrowed
								? 'No audited actions match the current filter.'
								: 'Audited actions (who did what) will appear here as your team operates the platform.'
						}
					/>
				) : (
					<LogList
						ariaLabel="Audit log"
						columns={{ actor: 'Who', subject: 'Target', detail: 'Via' }}
						actionWidth="3.5rem"
					>
						{query.isLoading ? (
							<SkeletonRows rows={6} className="px-4" />
						) : (
							days.map((day) => (
								<LogDay key={day.key} label={day.label}>
									{day.items.map((row) => {
										const tone = auditTone(row);
										const sentence = auditSentence(row);
										return (
											<LogRow
												key={row.id}
												tsMs={Date.parse(row.occurred_at)}
												tone={tone}
												statusLabel={TONE_LABEL[tone]}
												title={sentence}
												secondary={
													row.reason ??
													auditChangeSummary(auditChanges(row)) ??
													undefined
												}
												actor={
													row.actor_id ? (
														<ActorLabel
															actorId={row.actor_id}
															actorType={row.actor_type}
														/>
													) : (
														row.actor_type
													)
												}
												subject={
													<>
														<span className="shrink-0">
															{auditTargetLabel(row.target_type)}
														</span>
														<span className="text-muted-foreground/80 truncate font-mono">
															{row.target_id}
														</span>
													</>
												}
												detail={
													row.origin ? (
														<span className="font-sans">
															{originLabel(row.origin)}
														</span>
													) : undefined
												}
												action={<CrossLink row={row} />}
												label={sentence}
												active={
													detail?.kind === 'audit' && detail.id === row.id
												}
												onOpen={() => open({ kind: 'audit', id: row.id })}
											/>
										);
									})}
								</LogDay>
							))
						)}
					</LogList>
				)}

				{traceId && !showEmpty && (
					<p className="text-muted-foreground text-xs">
						Filtering the visible page by trace{' '}
						<span className="font-mono">{traceId}</span> (the audit API has no trace
						filter, so paging steps through unfiltered pages).
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
		</LogLayout>
	);
}
