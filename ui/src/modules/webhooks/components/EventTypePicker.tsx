/**
 * EventTypePicker — choose which relayable platform events an endpoint receives.
 *
 * Replaces the old comma-separated free-text field. Every option comes from the
 * curated catalog (`@/modules/webhooks/api`), which mirrors the backend's real
 * emitted types, so a user can only ever subscribe to something the platform can
 * actually deliver.
 *
 * The load-bearing subtlety is the empty state: the backend treats an endpoint
 * with **no** subscribed types as "everything relayable" (see the fan-out
 * denylist). So "select nothing" is a real, useful choice — not an error — and
 * the component says so out loud rather than nudging the user to pick something.
 */
import { useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import { Badge, Button, Checkbox, Input } from '@/shared/ui';
import {
	WEBHOOK_EVENT_CATALOG,
	WEBHOOK_EVENT_CATEGORY_LABELS,
	type WebhookEventCategory,
	type WebhookEventTypeInfo,
} from '@/modules/webhooks/api';

interface EventTypePickerProps {
	/** Currently selected `event_type` strings. Empty = subscribe to all. */
	value: string[];
	onChange: (next: string[]) => void;
	/** Id of the group label, for `aria-labelledby` on the list. */
	labelId?: string;
}

function groupByCategory(
	events: readonly WebhookEventTypeInfo[],
): [WebhookEventCategory, WebhookEventTypeInfo[]][] {
	const groups = new Map<WebhookEventCategory, WebhookEventTypeInfo[]>();
	for (const event of events) {
		const bucket = groups.get(event.category) ?? [];
		bucket.push(event);
		groups.set(event.category, bucket);
	}
	return [...groups.entries()];
}

export function EventTypePicker({ value, onChange, labelId }: EventTypePickerProps) {
	const [query, setQuery] = useState('');
	const selected = useMemo(() => new Set(value), [value]);

	const filtered = useMemo(() => {
		const q = query.trim().toLowerCase();
		if (!q) return WEBHOOK_EVENT_CATALOG;
		return WEBHOOK_EVENT_CATALOG.filter(
			(e) =>
				e.type.toLowerCase().includes(q) ||
				e.label.toLowerCase().includes(q) ||
				e.description.toLowerCase().includes(q),
		);
	}, [query]);

	const groups = useMemo(() => groupByCategory(filtered), [filtered]);

	function toggle(type: string) {
		const next = new Set(selected);
		if (next.has(type)) next.delete(type);
		else next.add(type);
		// Preserve catalog order so the stored list is stable and readable.
		onChange(WEBHOOK_EVENT_CATALOG.filter((e) => next.has(e.type)).map((e) => e.type));
	}

	const allSelected = value.length === 0;

	return (
		<div className="space-y-3">
			<div
				className="border-border bg-muted/40 flex items-start gap-2 rounded-lg border px-3 py-2"
				aria-live="polite"
			>
				<p className="text-muted-foreground text-xs leading-relaxed">
					{allSelected ? (
						<>
							<span className="text-foreground font-medium">
								Subscribed to every relayable event.
							</span>{' '}
							Leave everything unchecked to keep receiving all of them, or select
							specific types below to narrow the feed.
						</>
					) : (
						<>
							<span className="text-foreground font-medium">
								{value.length} event type{value.length === 1 ? '' : 's'} selected.
							</span>{' '}
							Clear the selection to fall back to receiving every relayable event.
						</>
					)}
				</p>
				{!allSelected && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={() => onChange([])}
						className="shrink-0"
					>
						Clear
					</Button>
				)}
			</div>

			<div className="relative">
				<Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
				<Input
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					placeholder="Filter events…"
					aria-label="Filter event types"
					className="pl-9"
				/>
			</div>

			<div
				role="group"
				aria-labelledby={labelId}
				className="border-border max-h-72 space-y-4 overflow-y-auto rounded-lg border p-3"
			>
				{groups.length === 0 ? (
					<p className="text-muted-foreground py-6 text-center text-sm">
						No event types match &ldquo;{query}&rdquo;.
					</p>
				) : (
					groups.map(([category, events]) => (
						<div key={category} className="space-y-1.5">
							<p className="text-muted-foreground text-xs font-medium tracking-wider uppercase">
								{WEBHOOK_EVENT_CATEGORY_LABELS[category]}
							</p>
							<ul className="space-y-0.5">
								{events.map((event) => {
									const isChecked = selected.has(event.type);
									return (
										<li key={event.type}>
											<Checkbox
												checked={isChecked}
												onChange={() => toggle(event.type)}
												ariaLabel={event.label}
												className="hover:bg-muted/60 w-full items-start rounded-md p-2"
											>
												<span className="block">
													<span className="text-foreground flex flex-wrap items-center gap-1.5 text-sm font-medium">
														<code className="font-mono text-xs">
															{event.type}
														</code>
														{event.actionable && (
															<Badge variant="warning">
																needs action
															</Badge>
														)}
													</span>
													<span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
														{event.description}
													</span>
												</span>
											</Checkbox>
										</li>
									);
								})}
							</ul>
						</div>
					))
				)}
			</div>
		</div>
	);
}
