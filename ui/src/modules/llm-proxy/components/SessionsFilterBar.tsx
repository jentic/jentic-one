/**
 * Filter controls for the Sessions table. Filtering is applied client-side by
 * the page; this bar only surfaces the controls and reports changes. State is
 * owned by `useSessionFilters` (URL search params) so views stay deep-linkable.
 */
import { X } from 'lucide-react';
import { Button, SearchInput, Select } from '@/shared/ui';

interface SessionsFilterBarProps {
	q: string;
	status: string;
	api: string;
	statuses: string[];
	apis: string[];
	active: boolean;
	onQueryChange: (value: string) => void;
	onStatusChange: (value: string) => void;
	onApiChange: (value: string) => void;
	onReset: () => void;
}

function titleCase(value: string): string {
	return value.charAt(0).toUpperCase() + value.slice(1);
}

export function SessionsFilterBar({
	q,
	status,
	api,
	statuses,
	apis,
	active,
	onQueryChange,
	onStatusChange,
	onApiChange,
	onReset,
}: SessionsFilterBarProps) {
	return (
		<div className="flex items-center gap-2">
			<div className="w-40 shrink-0">
				<Select
					value={status}
					onChange={(e) => onStatusChange(e.target.value)}
					aria-label="Filter by status"
				>
					<option value="all">All statuses</option>
					{statuses.map((s) => (
						<option key={s} value={s}>
							{titleCase(s)}
						</option>
					))}
				</Select>
			</div>
			<div className="w-48 shrink-0">
				<Select
					value={api}
					onChange={(e) => onApiChange(e.target.value)}
					aria-label="Filter by API"
				>
					<option value="all">All APIs</option>
					{apis.map((a) => (
						<option key={a} value={a}>
							{a}
						</option>
					))}
				</Select>
			</div>
			{active && (
				<Button
					variant="ghost"
					size="sm"
					onClick={onReset}
					className="text-muted-foreground hover:text-foreground shrink-0 gap-1"
				>
					<X className="h-3.5 w-3.5" />
					Clear
				</Button>
			)}
			<div className="ml-auto w-64 shrink">
				<SearchInput
					value={q}
					onValueChange={onQueryChange}
					placeholder="Search sessions…"
					size="sm"
					aria-label="Search sessions"
				/>
			</div>
		</div>
	);
}
