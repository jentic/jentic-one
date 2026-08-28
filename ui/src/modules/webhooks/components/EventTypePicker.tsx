import { useMemo, useState } from 'react';
import { Filter } from 'lucide-react';
import { Checkbox, SearchInput } from '@/shared/ui';
import type { EventTypeDef } from '@/modules/webhooks/api';

/**
 * Grouped multi-select over the platform event catalog, with an "all events"
 * wildcard toggle. `value` is the wire shape: `['*']` for everything, or an
 * explicit list of event types.
 */
export function EventTypePicker({
	catalog,
	value,
	onChange,
}: {
	catalog: EventTypeDef[];
	value: string[];
	onChange: (next: string[]) => void;
}) {
	const [query, setQuery] = useState('');
	const allSelected = value.includes('*');
	const selected = useMemo(() => new Set(value), [value]);

	const groups = useMemo(() => {
		const q = query.trim().toLowerCase();
		const filtered = q
			? catalog.filter(
					(e) =>
						e.type.toLowerCase().includes(q) || e.description.toLowerCase().includes(q),
				)
			: catalog;
		const byGroup = new Map<string, EventTypeDef[]>();
		for (const entry of filtered) {
			const bucket = byGroup.get(entry.group) ?? [];
			bucket.push(entry);
			byGroup.set(entry.group, bucket);
		}
		return [...byGroup.entries()];
	}, [catalog, query]);

	const toggle = (type: string, checked: boolean) => {
		const next = new Set(selected);
		next.delete('*');
		if (checked) next.add(type);
		else next.delete(type);
		onChange([...next]);
	};

	return (
		<div className="space-y-3">
			<Checkbox
				checked={allSelected}
				onChange={(checked) => onChange(checked ? ['*'] : [])}
				ariaLabel="Subscribe to all events"
			>
				<span className="text-foreground font-medium">All events</span> — including event
				types added in future releases.
			</Checkbox>
			{!allSelected && (
				<>
					<SearchInput
						value={query}
						onValueChange={setQuery}
						icon={<Filter className="h-3.5 w-3.5" />}
						placeholder="Filter event types…"
						aria-label="Filter event types"
						disabled={catalog.length === 0}
					/>
					<div className="border-border max-h-64 space-y-3 overflow-y-auto rounded-lg border p-3">
						{groups.length === 0 && (
							<p className="text-muted-foreground py-2 text-center text-sm">
								No event types match.
							</p>
						)}
						{groups.map(([group, entries]) => (
							<div key={group} className="space-y-1.5">
								<p className="text-muted-foreground font-mono text-[11px] tracking-wider uppercase">
									{group}
								</p>
								{entries.map((entry) => (
									<Checkbox
										key={entry.type}
										checked={selected.has(entry.type)}
										onChange={(checked) => toggle(entry.type, checked)}
										size="sm"
									>
										<span className="text-foreground font-mono text-xs">
											{entry.type}
										</span>{' '}
										<span className="text-muted-foreground text-xs">
											— {entry.description}
										</span>
									</Checkbox>
								))}
							</div>
						))}
					</div>
				</>
			)}
		</div>
	);
}
