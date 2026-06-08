import { type JSX, useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { JobsFilters } from './JobsFilters';
import { JobsTable } from './JobsTable';
import { JobDetailSheet } from './JobDetailSheet';
import { api } from '@/api/client';
import type { JobOut } from '@/api/types';
import type { JobKindFilter, JobLogEntry, JobStatusFilter } from '@/components/monitor/types';
import { jobToJobLogEntry } from '@/lib/monitor-transformers';

interface FilterOption {
	value: string;
	label: string;
}

interface JobsTabProps {
	statusFilter: JobStatusFilter;
	kindFilter: JobKindFilter;
	toolkitFilter: string | null;
	agentFilter: string | null;
	/** Raw, undebounced value bound to the controlled search input so typing
	 * feels instant. Drives display only — NOT the query. */
	searchQuery: string;
	/** Debounced/committed search value that actually drives the jobs query.
	 * Threading this separately keeps the input echo immediate while the
	 * network request (and queryKey churn) only fires once typing settles. */
	searchQueryDebounced: string;
	page: number;
	pageSize: number;
	since: number;
	toolkitOptions: FilterOption[];
	agentOptions: FilterOption[];
	toolkitNameById: Map<string, string>;
	agentNameById: Map<string, string>;
	showAgentFilter: boolean;
	hasFilters: boolean;
	onStatusChange: (next: JobStatusFilter) => void;
	onKindChange: (next: JobKindFilter) => void;
	onToolkitChange: (next: string | null) => void;
	onAgentChange: (next: string | null) => void;
	onSearchChange: (q: string) => void;
	onClearFilters: () => void;
	onPageChange: (page: number) => void;
	onOpenTrace?: (traceId: string) => void;
	/** Deep-link: when set, JobsTab opens the drawer for this job once the
	 * row appears in the polling query. Driven by the URL so refresh and
	 * cross-link from the Execution Log drawer both work. */
	selectedJobId?: string | null;
	/** Notifies the parent when the user opens or closes a job drawer.
	 * The parent owns the URL param so back/forward and shareable deep
	 * links stay consistent. */
	onSelectionChange?: (jobId: string | null) => void;
}

const POLL_INTERVAL_MS = 15_000;

/**
 * Translate the UI's `JobStatusFilter` into the backend's `?status=` query
 * shape. The backend understands a comma-separated list (`status IN (...)`),
 * which we exploit for the synthetic "in-flight" filter (pending+running)
 * surfaced as a single dropdown choice in `JobsFilters`. The `active_now`
 * pill on the Monitor page header routes here too.
 *
 * `cancelled` is not a backend value (cancelled rows store `status='failed'`),
 * so we drop it here and let the table-level transform tag the row instead.
 * The user opted for "no separate cancelled filter" — see types.ts.
 */
function statusFilterToBackend(filter: JobStatusFilter): string | undefined {
	if (filter === 'all') return undefined;
	if (filter === 'inflight') return 'pending,running';
	return filter;
}

function kindFilterToBackend(filter: JobKindFilter): 'workflow' | 'broker' | undefined {
	return filter === 'all' ? undefined : filter;
}

export function JobsTab({
	statusFilter,
	kindFilter,
	toolkitFilter,
	agentFilter,
	searchQuery,
	searchQueryDebounced,
	page,
	pageSize,
	since,
	toolkitOptions,
	agentOptions,
	toolkitNameById,
	agentNameById,
	showAgentFilter,
	hasFilters,
	onStatusChange,
	onKindChange,
	onToolkitChange,
	onAgentChange,
	onSearchChange,
	onClearFilters,
	onPageChange,
	onOpenTrace,
	selectedJobId,
	onSelectionChange,
}: JobsTabProps): JSX.Element {
	const queryClient = useQueryClient();
	const [selected, setSelected] = useState<JobLogEntry | null>(null);
	const [isSheetOpen, setIsSheetOpen] = useState(false);

	const jobsQuery = useQuery({
		queryKey: [
			'monitor',
			'jobs',
			'tab',
			page,
			pageSize,
			statusFilter,
			kindFilter,
			toolkitFilter,
			agentFilter,
			searchQueryDebounced,
			since,
		],
		queryFn: () =>
			api.listJobs({
				status: statusFilterToBackend(statusFilter),
				kind: kindFilterToBackend(kindFilter),
				page,
				limit: pageSize,
				toolkitId: toolkitFilter,
				agentId: agentFilter,
				since,
				// Empty/whitespace short-circuits to undefined so the no-search
				// query plan stays identical to the current one — backend has
				// the same guard but skipping the param keeps queryKey churn
				// proportional to user intent.
				q: searchQueryDebounced.trim() ? searchQueryDebounced.trim() : undefined,
			}),
		// Polling — same cadence as the in-flight execution log so the user
		// experiences consistent "freshness" across tabs. 15s is the sweet
		// spot: faster looks like flicker on this density of data; slower
		// makes the cancel button feel laggy.
		refetchInterval: POLL_INTERVAL_MS,
	});

	const cancelMutation = useMutation({
		mutationFn: (jobId: string) => api.cancelJob(jobId),
		onSuccess: (_data, jobId) => {
			// Optimistically reflect the new status in the open sheet so the
			// user sees feedback before the next poll arrives.
			setSelected((prev) =>
				prev && prev.jobId === jobId ? { ...prev, status: 'cancelled' } : prev,
			);
			void queryClient.invalidateQueries({ queryKey: ['monitor', 'jobs'] });
		},
	});

	const entries: JobLogEntry[] = useMemo(() => {
		const data = (jobsQuery.data as unknown as { data?: JobOut[] } | undefined)?.data ?? [];
		return data.map((j) => jobToJobLogEntry(j, toolkitNameById, agentNameById));
	}, [jobsQuery.data, toolkitNameById, agentNameById]);

	const totalCount =
		(jobsQuery.data as unknown as { total?: number } | undefined)?.total ?? entries.length;

	const handleRowClick = (entry: JobLogEntry) => {
		setSelected(entry);
		setIsSheetOpen(true);
		onSelectionChange?.(entry.jobId);
	};

	const handleSheetClose = () => {
		setIsSheetOpen(false);
		onSelectionChange?.(null);
	};

	// Deep-link: open the drawer when `selectedJobId` arrives in the props
	// (e.g. coming from `?job=…` on first paint or from a cross-link in the
	// Execution Log drawer). We wait for the row to appear in the polling
	// query so the header has the same shape as a click-driven open. While
	// waiting, isSheetOpen stays false — better than rendering an empty
	// drawer.
	useEffect(() => {
		if (!selectedJobId) {
			// Parent cleared the deep link (e.g. user closed sheet via URL
			// back-button). Mirror that into local sheet state.
			if (isSheetOpen) setIsSheetOpen(false);
			return;
		}
		if (selected?.jobId === selectedJobId && isSheetOpen) return;
		const match = entries.find((e) => e.jobId === selectedJobId);
		if (match) {
			setSelected(match);
			setIsSheetOpen(true);
		}
		// `entries` changes on every poll; the early-return guard above keeps
		// us from re-opening on each refresh when the user has already seen
		// the drawer. Local `selected`/`isSheetOpen` are intentionally not
		// in deps — they're guards, not triggers.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [selectedJobId, entries]);

	const handleCancel = (jobId: string) => {
		cancelMutation.mutate(jobId);
	};

	const canCancel =
		selected !== null && (selected.status === 'pending' || selected.status === 'running');

	return (
		<motion.div
			className="space-y-4"
			initial={{ opacity: 0, y: 12 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ duration: 0.3, ease: 'easeOut', delay: 0.1 }}
		>
			<JobsFilters
				statusFilter={statusFilter}
				kindFilter={kindFilter}
				toolkitFilter={toolkitFilter}
				agentFilter={agentFilter}
				searchQuery={searchQuery}
				toolkitOptions={toolkitOptions}
				agentOptions={agentOptions}
				showAgentFilter={showAgentFilter}
				hasFilters={hasFilters}
				onStatusChange={onStatusChange}
				onKindChange={onKindChange}
				onToolkitChange={onToolkitChange}
				onAgentChange={onAgentChange}
				onSearchChange={onSearchChange}
				onClearFilters={onClearFilters}
			/>

			<JobsTable
				jobs={entries}
				totalCount={totalCount}
				page={page}
				pageSize={pageSize}
				isLoading={jobsQuery.isLoading}
				onRowClick={handleRowClick}
				onPageChange={onPageChange}
			/>

			<JobDetailSheet
				job={selected}
				isOpen={isSheetOpen}
				isLoading={false}
				isCancelling={cancelMutation.isPending}
				canCancel={canCancel}
				onClose={handleSheetClose}
				onCancel={handleCancel}
				onOpenTrace={onOpenTrace}
			/>
		</motion.div>
	);
}
