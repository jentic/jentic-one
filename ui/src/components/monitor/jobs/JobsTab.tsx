import { type JSX, useMemo, useState } from 'react';
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
	onClearFilters: () => void;
	onPageChange: (page: number) => void;
	onOpenTrace?: (traceId: string) => void;
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
	onClearFilters,
	onPageChange,
	onOpenTrace,
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
	};

	const handleSheetClose = () => {
		setIsSheetOpen(false);
	};

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
				toolkitOptions={toolkitOptions}
				agentOptions={agentOptions}
				showAgentFilter={showAgentFilter}
				hasFilters={hasFilters}
				onStatusChange={onStatusChange}
				onKindChange={onKindChange}
				onToolkitChange={onToolkitChange}
				onAgentChange={onAgentChange}
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
