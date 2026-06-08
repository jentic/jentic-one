import { type JSX } from 'react';
import { motion } from 'framer-motion';
import { ExecutionFilters } from './ExecutionFilters';
import { ExecutionTable } from './ExecutionTable';
import type { ExecutionLogEntry, ExecutionStatusFilter } from '@/components/monitor/types';

interface FilterOption {
	value: string;
	label: string;
}

interface ExecutionLogTabProps {
	executions: ExecutionLogEntry[];
	totalCount: number;
	page: number;
	pageSize: number;
	isLoading: boolean;
	statusFilter: ExecutionStatusFilter;
	toolkitFilter: string | null;
	apiFilter: string | null;
	agentFilter: string | null;
	searchQuery: string;
	toolkitOptions: FilterOption[];
	apiOptions: FilterOption[];
	agentOptions: FilterOption[];
	showAgentFilter: boolean;
	hasFilters: boolean;
	onStatusChange: (status: ExecutionStatusFilter) => void;
	onToolkitChange: (toolkitId: string | null) => void;
	onApiChange: (apiKey: string | null) => void;
	onAgentChange: (agentId: string | null) => void;
	onSearchChange: (q: string) => void;
	onClearFilters: () => void;
	onRowClick: (execution: ExecutionLogEntry) => void;
	onPageChange: (page: number) => void;
	/** When set, JobBadge cells become cross-links to the Jobs tab. */
	onOpenJob?: (jobId: string) => void;
}

export function ExecutionLogTab({
	executions,
	totalCount,
	page,
	pageSize,
	isLoading,
	statusFilter,
	toolkitFilter,
	apiFilter,
	agentFilter,
	searchQuery,
	toolkitOptions,
	apiOptions,
	agentOptions,
	showAgentFilter,
	hasFilters,
	onStatusChange,
	onToolkitChange,
	onApiChange,
	onAgentChange,
	onSearchChange,
	onClearFilters,
	onRowClick,
	onPageChange,
	onOpenJob,
}: ExecutionLogTabProps): JSX.Element {
	return (
		<motion.div
			className="space-y-4"
			initial={{ opacity: 0, y: 12 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{ duration: 0.3, ease: 'easeOut', delay: 0.1 }}
		>
			<ExecutionFilters
				statusFilter={statusFilter}
				toolkitFilter={toolkitFilter}
				apiFilter={apiFilter}
				agentFilter={agentFilter}
				searchQuery={searchQuery}
				toolkitOptions={toolkitOptions}
				apiOptions={apiOptions}
				agentOptions={agentOptions}
				showAgentFilter={showAgentFilter}
				onStatusChange={onStatusChange}
				onToolkitChange={onToolkitChange}
				onApiChange={onApiChange}
				onAgentChange={onAgentChange}
				onSearchChange={onSearchChange}
				onClearFilters={onClearFilters}
				hasFilters={hasFilters}
			/>

			<ExecutionTable
				executions={executions}
				totalCount={totalCount}
				page={page}
				pageSize={pageSize}
				isLoading={isLoading}
				onRowClick={onRowClick}
				onPageChange={onPageChange}
				onOpenJob={onOpenJob}
			/>
		</motion.div>
	);
}
