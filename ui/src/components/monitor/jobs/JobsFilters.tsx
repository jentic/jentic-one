import { type JSX } from 'react';
import { X, ChevronDown, Filter } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { SearchInput } from '@/components/ui/SearchInput';
import type { JobKindFilter, JobStatusFilter } from '@/components/monitor/types';

interface FilterOption {
	value: string;
	label: string;
}

interface JobsFiltersProps {
	statusFilter: JobStatusFilter;
	kindFilter: JobKindFilter;
	toolkitFilter: string | null;
	agentFilter: string | null;
	searchQuery: string;
	toolkitOptions: FilterOption[];
	agentOptions: FilterOption[];
	showAgentFilter: boolean;
	hasFilters: boolean;
	onStatusChange: (status: JobStatusFilter) => void;
	onKindChange: (kind: JobKindFilter) => void;
	onToolkitChange: (toolkitId: string | null) => void;
	onAgentChange: (agentId: string | null) => void;
	onSearchChange: (q: string) => void;
	onClearFilters: () => void;
	className?: string;
}

const STATUS_OPTIONS: Array<{ value: JobStatusFilter; label: string }> = [
	{ value: 'all', label: 'All' },
	{ value: 'inflight', label: 'In-flight' },
	{ value: 'pending', label: 'Pending' },
	{ value: 'running', label: 'Running' },
	{ value: 'complete', label: 'Complete' },
	{ value: 'failed', label: 'Failed' },
	{ value: 'upstream_async', label: 'Upstream' },
];

const KIND_OPTIONS: Array<{ value: JobKindFilter; label: string }> = [
	{ value: 'all', label: 'All kinds' },
	{ value: 'workflow', label: 'Workflows' },
	{ value: 'broker', label: 'Broker calls' },
];

function FilterDropdown({
	label,
	value,
	options,
	onChange,
}: {
	label: string;
	value: string | null;
	options: FilterOption[];
	onChange: (val: string | null) => void;
}): JSX.Element {
	return (
		<div className="relative">
			<select
				value={value ?? ''}
				onChange={(e) => onChange(e.target.value || null)}
				aria-label={label}
				className={cn(
					'border-border bg-card h-8 cursor-pointer appearance-none rounded-lg border pr-8 pl-3 text-xs font-medium transition-colors',
					'focus:border-primary focus:ring-primary focus:ring-1 focus:outline-none',
					value ? 'text-foreground' : 'text-muted-foreground',
				)}
			>
				<option value="">{label}</option>
				{options.map((option) => (
					<option key={option.value} value={option.value}>
						{option.label}
					</option>
				))}
			</select>
			<ChevronDown
				aria-hidden="true"
				className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 h-3.5 w-3.5 -translate-y-1/2"
			/>
		</div>
	);
}

export function JobsFilters({
	statusFilter,
	kindFilter,
	toolkitFilter,
	agentFilter,
	searchQuery,
	toolkitOptions,
	agentOptions,
	showAgentFilter,
	hasFilters,
	onStatusChange,
	onKindChange,
	onToolkitChange,
	onAgentChange,
	onSearchChange,
	onClearFilters,
	className,
}: JobsFiltersProps): JSX.Element {
	return (
		<div
			className={cn(
				'space-y-3 sm:flex sm:flex-wrap sm:items-center sm:gap-3 sm:space-y-0',
				className,
			)}
		>
			<div className="overflow-x-auto">
				<SegmentedToggle
					layoutId="jobsStatusToggle"
					options={STATUS_OPTIONS}
					value={statusFilter}
					onChange={onStatusChange}
					className="w-max [&>button]:whitespace-nowrap"
				/>
			</div>

			<div className="bg-border hidden h-5 w-px sm:block" />

			<div className="flex flex-wrap items-center gap-2">
				<SearchInput
					value={searchQuery}
					onValueChange={onSearchChange}
					size="sm"
					icon={<Filter className="h-3.5 w-3.5" />}
					placeholder="Filter jobs, agents, upstream URL…"
					aria-label="Filter jobs"
					className="w-full sm:w-56"
				/>

				<SegmentedToggle
					layoutId="jobsKindToggle"
					options={KIND_OPTIONS}
					value={kindFilter}
					onChange={onKindChange}
					className="[&>button]:whitespace-nowrap"
				/>

				<FilterDropdown
					label="All toolkits"
					value={toolkitFilter}
					options={toolkitOptions}
					onChange={onToolkitChange}
				/>

				{showAgentFilter && (
					<FilterDropdown
						label={agentOptions.length === 0 ? 'No agents' : 'All agents'}
						value={agentFilter}
						options={agentOptions}
						onChange={onAgentChange}
					/>
				)}

				{hasFilters && (
					<button
						type="button"
						onClick={onClearFilters}
						className="border-border text-muted-foreground hover:bg-muted hover:text-foreground flex cursor-pointer items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors"
					>
						<X className="h-3 w-3" />
						Clear
					</button>
				)}
			</div>
		</div>
	);
}
