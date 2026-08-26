/**
 * EventTypePicker — choose which relayable platform events an endpoint receives.
 *
 * A GitHub-style grouped tree: every option comes from the curated catalog
 * (`@/modules/webhooks/api`), grouped by its `noun` prefix (the segment before
 * the first `.`), with a **tri-state parent** per noun (checked / indeterminate
 * / unchecked) that selects or clears the whole group at once. The catalog
 * mirrors the backend's real subscribable set, so a user can only ever pick
 * something the platform can actually deliver.
 *
 * The load-bearing subtlety is the empty state: the backend treats an endpoint
 * with **no** subscribed types as "everything relayable" (see the fan-out
 * denylist). So "select nothing" is a real, useful choice — the picker makes it
 * an explicit two-way switch:
 *
 *   - **Everything** — the stored list is empty; the endpoint receives every
 *     relayable event, now and as new types are added.
 *   - **Only specific types** — the tree is enabled and the stored list is
 *     exactly what is ticked. Emptying it here is a no-op the mode guards
 *     against (an empty list would silently mean "everything" again), so
 *     switching to this mode with nothing ticked keeps the tree open for a
 *     choice rather than collapsing back to "everything".
 *
 * The picker verifies its catalog against the backend-served list
 * (`useWebhookEventCatalog`) at runtime and warns if the two ever drift — a
 * belt-and-braces companion to the build-time drift test.
 */
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ChevronRight, Minus, Search } from 'lucide-react';
import { Badge, Checkbox, Input } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	WEBHOOK_EVENT_CATALOG,
	groupEventsByNoun,
	useWebhookEventCatalog,
	type WebhookEventTypeInfo,
} from '@/modules/webhooks/api';

interface EventTypePickerProps {
	/** Currently selected `event_type` strings. Empty = subscribe to all. */
	value: string[];
	onChange: (next: string[]) => void;
	/** Id of the group label, for `aria-labelledby` on the tree. */
	labelId?: string;
}

/** Preserve catalog order so the stored list stays stable and readable. */
function orderBySelection(selected: ReadonlySet<string>): string[] {
	return WEBHOOK_EVENT_CATALOG.filter((e) => selected.has(e.type)).map((e) => e.type);
}

export function EventTypePicker({ value, onChange, labelId }: EventTypePickerProps) {
	const [query, setQuery] = useState('');
	const selected = useMemo(() => new Set(value), [value]);
	const everything = value.length === 0;

	// "Everything" is derived from an empty list, but the user can also be in
	// "specific" mode with nothing ticked yet (mid-choice). Track that intent
	// locally so an in-progress empty selection doesn't read as "everything".
	const [specificMode, setSpecificMode] = useState(!everything);
	// Re-sync the mode to the incoming value whenever it flips to/from empty —
	// e.g. seeding the edit form (→ specific) or reopening for a fresh "New"
	// endpoint (→ everything). Without the empty→everything direction, the picker
	// would stay stuck in "specific" after an edit. The user's own mid-choice
	// (clicking "Only specific types" with nothing ticked) sets `specificMode`
	// directly, so it isn't lost — only a genuine value change re-syncs here.
	useEffect(() => {
		setSpecificMode(!everything);
	}, [everything]);

	// Cross-check the curated catalog against the backend's served list. This is
	// the runtime half of the drift guard; the unit test is the build-time half.
	const { data: backendTypes } = useWebhookEventCatalog();
	const drift = useMemo(() => {
		if (!backendTypes) return null;
		const be = new Set(backendTypes);
		const fe = new Set(WEBHOOK_EVENT_CATALOG.map((e) => e.type));
		const missing = backendTypes.filter((t) => !fe.has(t));
		const extra = [...fe].filter((t) => !be.has(t));
		return missing.length === 0 && extra.length === 0 ? null : { missing, extra };
	}, [backendTypes]);

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

	const groups = useMemo(() => groupEventsByNoun(filtered), [filtered]);

	function setSelection(next: Set<string>) {
		onChange(orderBySelection(next));
	}

	function toggleOne(type: string) {
		const next = new Set(selected);
		if (next.has(type)) next.delete(type);
		else next.add(type);
		setSelection(next);
	}

	function toggleGroup(events: WebhookEventTypeInfo[], allOn: boolean) {
		const next = new Set(selected);
		for (const e of events) {
			if (allOn) next.delete(e.type);
			else next.add(e.type);
		}
		setSelection(next);
	}

	function enterEverything() {
		setSpecificMode(false);
		onChange([]);
	}

	function enterSpecific() {
		setSpecificMode(true);
	}

	return (
		<div className="space-y-3">
			{drift && (
				<div
					className="border-warning/40 bg-warning/5 text-warning flex items-start gap-2 rounded-lg border px-3 py-2 text-xs"
					role="status"
				>
					<AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
					<span>
						The event list may be out of date with the platform. You can still subscribe
						to every relayable event by choosing “Everything”.
					</span>
				</div>
			)}

			{/* The two-way mode switch (GitHub's "everything / select individual"). */}
			<div
				role="radiogroup"
				aria-label="Event subscription mode"
				className="grid grid-cols-1 gap-2 sm:grid-cols-2"
			>
				<button
					type="button"
					role="radio"
					aria-checked={!specificMode}
					onClick={enterEverything}
					className={cn(
						'focus-visible:ring-ring rounded-lg border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none',
						!specificMode
							? 'border-primary bg-primary/5'
							: 'border-border hover:border-foreground/30',
					)}
				>
					<span className="text-foreground block text-sm font-medium">Everything</span>
					<span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
						Receive every relayable event, including new types added later.
					</span>
				</button>
				<button
					type="button"
					role="radio"
					aria-checked={specificMode}
					onClick={enterSpecific}
					className={cn(
						'focus-visible:ring-ring rounded-lg border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none',
						specificMode
							? 'border-primary bg-primary/5'
							: 'border-border hover:border-foreground/30',
					)}
				>
					<span className="text-foreground block text-sm font-medium">
						Only specific types
					</span>
					<span className="text-muted-foreground mt-0.5 block text-xs leading-relaxed">
						{specificMode && value.length > 0
							? `${value.length} type${value.length === 1 ? '' : 's'} selected.`
							: 'Pick individual events from the list below.'}
					</span>
				</button>
			</div>

			{specificMode && (
				<>
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
						className="border-border max-h-72 space-y-3 overflow-y-auto rounded-lg border p-3"
					>
						{groups.length === 0 ? (
							<p className="text-muted-foreground py-6 text-center text-sm">
								No event types match &ldquo;{query}&rdquo;.
							</p>
						) : (
							groups.map(({ noun, events }) => {
								const selectedInGroup = events.filter((e) =>
									selected.has(e.type),
								).length;
								const allOn = selectedInGroup === events.length;
								const someOn = selectedInGroup > 0 && !allOn;
								return (
									<fieldset key={noun} className="space-y-1">
										<legend className="sr-only">{noun} events</legend>
										{/* Tri-state parent: checked / indeterminate / off. */}
										<div className="flex items-center gap-2">
											<button
												type="button"
												role="checkbox"
												aria-checked={
													someOn ? 'mixed' : allOn ? 'true' : 'false'
												}
												aria-label={`All ${noun} events`}
												onClick={() => toggleGroup(events, allOn)}
												className="group flex items-center gap-2 rounded p-1"
											>
												<span
													className={cn(
														'flex h-5 w-5 items-center justify-center rounded border transition-colors',
														allOn || someOn
															? 'border-primary bg-primary'
															: 'border-border group-hover:border-foreground/50 border-2',
													)}
												>
													{allOn && (
														<svg
															className="text-primary-foreground h-3.5 w-3.5"
															viewBox="0 0 20 20"
															fill="currentColor"
															aria-hidden="true"
														>
															<path
																fillRule="evenodd"
																d="M16.7 5.3a1 1 0 010 1.4l-7.5 7.5a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 111.4-1.4l2.8 2.79 6.8-6.79a1 1 0 011.4 0z"
																clipRule="evenodd"
															/>
														</svg>
													)}
													{someOn && (
														<Minus className="text-primary-foreground h-3.5 w-3.5" />
													)}
												</span>
												<span className="text-foreground font-mono text-xs font-semibold tracking-wider uppercase">
													{noun}
												</span>
											</button>
											<ChevronRight
												className="text-muted-foreground/40 h-3.5 w-3.5"
												aria-hidden="true"
											/>
											<span className="text-muted-foreground text-[11px]">
												{selectedInGroup}/{events.length}
											</span>
										</div>

										<ul className="ml-3 space-y-0.5 border-l pl-3">
											{events.map((event) => (
												<li key={event.type}>
													<Checkbox
														checked={selected.has(event.type)}
														onChange={() => toggleOne(event.type)}
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
											))}
										</ul>
									</fieldset>
								);
							})
						)}
					</div>
				</>
			)}
		</div>
	);
}
