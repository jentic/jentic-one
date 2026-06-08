import { type JSX } from 'react';
import { X, ChevronDown, Filter } from 'lucide-react';
import type { ExecutionStatusFilter } from '@/components/monitor/types';
import { cn } from '@/lib/utils';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { SearchInput } from '@/components/ui/SearchInput';

interface FilterOption {
	value: string;
	label: string;
}

interface ExecutionFiltersProps {
	statusFilter: ExecutionStatusFilter;
	toolkitFilter: string | null;
	apiFilter: string | null;
	agentFilter: string | null;
	searchQuery: string;
	toolkitOptions: FilterOption[];
	apiOptions: FilterOption[];
	agentOptions: FilterOption[];
	/** When false the agent picker is hidden — for non-human sessions. */
	showAgentFilter: boolean;
	onStatusChange: (status: ExecutionStatusFilter) => void;
	onToolkitChange: (toolkitId: string | null) => void;
	onApiChange: (apiKey: string | null) => void;
	onAgentChange: (agentId: string | null) => void;
	onSearchChange: (q: string) => void;
	onClearFilters: () => void;
	hasFilters: boolean;
	className?: string;
}

const STATUS_OPTIONS: Array<{ value: ExecutionStatusFilter; label: string }> = [
	{ value: 'ALL', label: 'All' },
	{ value: 'RUNNING', label: 'Running' },
	{ value: 'COMPLETED', label: 'Completed' },
	{ value: 'FAILED', label: 'Failed' },
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

export function ExecutionFilters({
	statusFilter,
	toolkitFilter,
	apiFilter,
	agentFilter,
	searchQuery,
	toolkitOptions,
	apiOptions,
	agentOptions,
	showAgentFilter,
	onStatusChange,
	onToolkitChange,
	onApiChange,
	onAgentChange,
	onSearchChange,
	onClearFilters,
	hasFilters,
	className,
}: ExecutionFiltersProps): JSX.Element {
	return (
		<div
			className={cn(
				'space-y-3 sm:flex sm:flex-wrap sm:items-center sm:gap-3 sm:space-y-0',
				className,
			)}
		>
			<div className="overflow-x-auto">
				<SegmentedToggle
					layoutId="execLogStatusToggle"
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
					placeholder="Filter workflows, APIs…"
					aria-label="Filter execution log"
					className="w-full sm:w-56"
				/>

				<FilterDropdown
					label="All toolkits"
					value={toolkitFilter}
					options={toolkitOptions}
					onChange={onToolkitChange}
				/>

				<FilterDropdown
					label="All APIs"
					value={apiFilter}
					options={apiOptions}
					onChange={onApiChange}
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
