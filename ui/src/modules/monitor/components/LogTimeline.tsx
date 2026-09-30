/**
 * LogTimeline — the activity histogram above the log.
 *
 * API traffic across the current window as stacked succeeded / failed bars,
 * from the usage endpoint's aggregate buckets (zero-filled — the endpoint
 * only returns buckets that saw a call). Drag across the bars (or click one)
 * to narrow the log to that span; the log zooms in and the chip beside the
 * header resets it. The window picker in the toolbar also clears a range.
 *
 * Pointer-only brushing is an enhancement: the same narrowing is reachable
 * from the keyboard through the window picker, and every bar's numbers are
 * in the group's text summary.
 */
import { useMemo, useRef, useState, type PointerEvent } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useUsageStats } from '@/modules/monitor/api';
import { useMonitorFilters, type TimeRange } from '@/modules/monitor/lib/useMonitorFilters';

/** "All" has no lower bound; the histogram shows the last 30 days of it. */
const ALL_WINDOW_DAYS = 30;
/** More bars than this and they stop reading as bars; re-bucket coarser. */
const MAX_BARS = 96;

interface Bar {
	startMs: number;
	endMs: number;
	success: number;
	failed: number;
}

function buildBars(
	sinceSec: number,
	untilSec: number,
	bucketSec: number,
	buckets: { ts: number; success: number; failed: number }[],
): Bar[] {
	if (bucketSec <= 0 || untilSec <= sinceSec) return [];
	const raw = Math.ceil((untilSec - sinceSec) / bucketSec);
	const factor = Math.max(1, Math.ceil(raw / MAX_BARS));
	const step = bucketSec * factor;
	const count = Math.ceil((untilSec - sinceSec) / step);
	const bars: Bar[] = Array.from({ length: count }, (_, i) => ({
		startMs: (sinceSec + i * step) * 1000,
		endMs: Math.min(sinceSec + (i + 1) * step, untilSec) * 1000,
		success: 0,
		failed: 0,
	}));
	for (const b of buckets) {
		const i = Math.floor((b.ts - sinceSec) / step);
		if (i < 0 || i >= count) continue;
		bars[i].success += b.success;
		bars[i].failed += b.failed;
	}
	return bars;
}

function formatTick(ms: number, spanMs: number): string {
	const d = new Date(ms);
	if (spanMs <= 36 * 3_600_000) {
		return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	if (spanMs <= 8 * 86_400_000) {
		return d.toLocaleString(undefined, {
			weekday: 'short',
			hour: '2-digit',
			minute: '2-digit',
		});
	}
	return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function formatRange(range: TimeRange): string {
	const span = range.toMs - range.fromMs;
	const sameDay = new Date(range.fromMs).toDateString() === new Date(range.toMs).toDateString();
	const day = (ms: number) =>
		new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	const time = (ms: number) =>
		new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	if (sameDay) return `${day(range.fromMs)}, ${time(range.fromMs)}–${time(range.toMs)}`;
	if (span < 3 * 86_400_000)
		return `${day(range.fromMs)} ${time(range.fromMs)} – ${day(range.toMs)} ${time(range.toMs)}`;
	return `${day(range.fromMs)} – ${day(range.toMs)}`;
}

export function LogTimeline() {
	const filters = useMonitorFilters();
	const { range, setRange, window: windowValue } = filters;

	// Anchor the window once per selection — recomputing "now" every render
	// would mint a new query key each time.
	const { sinceSec, untilSec } = useMemo(() => {
		if (range) {
			return {
				sinceSec: Math.floor(range.fromMs / 1000),
				untilSec: Math.ceil(range.toMs / 1000),
			};
		}
		const now = Math.floor(Date.now() / 1000);
		const days = windowValue === 'all' ? ALL_WINDOW_DAYS : Number(windowValue);
		return { sinceSec: now - days * 86_400, untilSec: now };
	}, [range, windowValue]);

	const query = useUsageStats({
		since: sinceSec,
		until: untilSec,
		topLimit: 1,
		agentId: filters.actorId ?? null,
	});
	const data = query.data;

	const bars = useMemo(
		() =>
			data
				? buildBars(data.since, data.until, data.bucket_seconds, data.buckets)
				: buildBars(sinceSec, untilSec, 3_600, []),
		[data, sinceSec, untilSec],
	);
	const max = Math.max(1, ...bars.map((b) => b.success + b.failed));
	// Unknown stays unknown: while loading (or after an error) the empty
	// placeholder bars must not read as "0 calls".
	const total = data?.stats.total ?? null;
	const failed = bars.reduce((n, b) => n + b.failed, 0);
	const status = query.isError ? 'Traffic unavailable' : 'Loading traffic…';

	// Brushing: indices into `bars` while a drag is in flight.
	const trackRef = useRef<HTMLDivElement>(null);
	const [brush, setBrush] = useState<{ a: number; b: number } | null>(null);
	const [hover, setHover] = useState<number | null>(null);

	const indexAt = (clientX: number) => {
		const el = trackRef.current;
		if (!el || bars.length === 0) return 0;
		const rect = el.getBoundingClientRect();
		const x = Math.min(Math.max(clientX - rect.left, 0), rect.width - 1);
		return Math.floor((x / rect.width) * bars.length);
	};
	const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
		if (e.button !== 0) return;
		e.currentTarget.setPointerCapture(e.pointerId);
		const i = indexAt(e.clientX);
		setBrush({ a: i, b: i });
	};
	const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
		const i = indexAt(e.clientX);
		setHover(i);
		if (brush) setBrush({ ...brush, b: i });
	};
	const onPointerUp = () => {
		if (!brush) return;
		const lo = Math.min(brush.a, brush.b);
		const hi = Math.max(brush.a, brush.b);
		setBrush(null);
		const fromMs = bars[lo]?.startMs;
		const toMs = bars[hi]?.endMs;
		// A single bucket that is already the whole range has nowhere to zoom.
		if (fromMs == null || toMs == null || (lo === 0 && hi === bars.length - 1)) return;
		setRange({ fromMs, toMs });
	};

	const spanMs = (untilSec - sinceSec) * 1000;
	const selLo = brush ? Math.min(brush.a, brush.b) : null;
	const selHi = brush ? Math.max(brush.a, brush.b) : null;
	const hovered = hover != null && !brush ? bars[hover] : null;
	const summary =
		total == null
			? `API traffic: ${status.toLowerCase()}`
			: `API traffic: ${total.toLocaleString()} calls, ${failed.toLocaleString()} failed${
					range ? `, ${formatRange(range)}` : ''
				}.`;

	return (
		<section
			role="group"
			aria-label="Activity timeline"
			className="border-border bg-card rounded-xl border px-3 pt-2.5 pb-2 sm:px-4"
		>
			<header className="flex min-h-7 flex-wrap items-center justify-between gap-x-3 gap-y-1">
				<p className="text-muted-foreground text-xs">
					<span className="text-foreground font-medium">API traffic</span>
					<span aria-hidden="true"> · </span>
					<span className="tabular-nums">
						{total == null ? status : `${total.toLocaleString()} calls`}
					</span>
					{total != null && failed > 0 && (
						<>
							<span aria-hidden="true"> · </span>
							<span className="text-danger tabular-nums">
								{failed.toLocaleString()} failed
							</span>
						</>
					)}
				</p>
				<div className="flex items-center gap-2">
					{range ? (
						<Button
							variant="outline"
							size="sm"
							className="h-7"
							onClick={() => setRange(null)}
							aria-label={`Clear time range ${formatRange(range)}`}
						>
							{formatRange(range)}
							<X className="h-3.5 w-3.5" aria-hidden="true" />
						</Button>
					) : (
						<span className="text-muted-foreground hidden text-[11px] sm:inline">
							Drag to zoom
						</span>
					)}
				</div>
			</header>

			<p className="sr-only">{summary}</p>

			<div className="relative mt-2" aria-hidden="true">
				<div
					ref={trackRef}
					className="flex h-14 cursor-crosshair touch-none items-end gap-px select-none"
					onPointerDown={onPointerDown}
					onPointerMove={onPointerMove}
					onPointerUp={onPointerUp}
					onPointerCancel={() => setBrush(null)}
					onPointerLeave={() => setHover(null)}
				>
					{bars.map((bar, i) => {
						const n = bar.success + bar.failed;
						const selected = selLo != null && selHi != null && i >= selLo && i <= selHi;
						const dim = selLo != null && !selected;
						return (
							<div
								key={bar.startMs}
								className={cn(
									'relative flex h-full min-w-0 flex-1 flex-col justify-end rounded-[2px] transition-opacity',
									selected && 'bg-primary/10',
									hover === i && !brush && 'bg-muted',
									dim && 'opacity-40',
								)}
							>
								{n === 0 ? (
									<div className="bg-border/70 h-px w-full" />
								) : (
									<div
										className="flex w-full flex-col overflow-hidden rounded-[2px]"
										style={{ height: `${Math.max(6, (n / max) * 100)}%` }}
									>
										{bar.failed > 0 && (
											<div
												className="bg-danger/85 w-full"
												style={{ flexGrow: bar.failed }}
											/>
										)}
										{bar.success > 0 && (
											<div
												className="bg-primary/55 w-full"
												style={{ flexGrow: bar.success }}
											/>
										)}
									</div>
								)}
							</div>
						);
					})}
				</div>

				{hovered && hover != null && (
					<div
						className="bg-popover text-popover-foreground border-border pointer-events-none absolute bottom-full z-10 mb-1.5 -translate-x-1/2 rounded-md border px-2 py-1 text-[11px] whitespace-nowrap shadow-md"
						style={{
							left: `clamp(4rem, ${((hover + 0.5) / bars.length) * 100}%, calc(100% - 4rem))`,
						}}
					>
						<p className="font-medium">{formatTick(hovered.startMs, spanMs)}</p>
						<p className="text-muted-foreground tabular-nums">
							{(hovered.success + hovered.failed).toLocaleString()} calls
							{hovered.failed > 0 && (
								<span className="text-danger"> · {hovered.failed} failed</span>
							)}
						</p>
					</div>
				)}

				<div className="text-muted-foreground mt-1 flex justify-between font-mono text-[10px] tabular-nums">
					<span>{formatTick(sinceSec * 1000, spanMs)}</span>
					<span>{range ? formatTick(untilSec * 1000, spanMs) : 'now'}</span>
				</div>
			</div>
		</section>
	);
}
