/**
 * "Per-session activity" — the Overview's primary chart. A hand-rolled
 * bar + line combo (no charting library, matching the Monitor module), one
 * group per session:
 *   - bars  = tokens consumed (left-hand scale — the larger-magnitude value)
 *   - line  = tool calls made in the session (right-hand counts scale)
 *   - line  = agents spawned (right-hand counts scale)
 *
 * This answers the question the sessions surface actually cares about —
 * "how do my agent runs compare in work done, spend, and fan-out?" — instead
 * of duplicating Monitor's calls-over-calendar-time view.
 *
 * A quiet "See more charts" button reveals two secondary hand-rolled charts
 * (calls by API, calls by HTTP method) so the default view stays clean.
 */
import { useId, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { BarChart3, Check, ChevronDown } from 'lucide-react';
import {
	Button,
	Card,
	CardBody,
	CardHeader,
	CardTitle,
	SegmentedToggle,
	type SegmentedToggleOption,
} from '@/shared/ui';
import { formatCount, formatTokens } from '@/modules/llm-proxy/lib/format';
import type { ProxySession } from '@/modules/llm-proxy/api';

/**
 * Time-window options, mirroring the Monitor Overview's "Usage" selector
 * (`SegmentedToggle` with 24h/7d/30d, defaulting to 7d). Values are the window
 * length in days, as strings, to match Monitor's convention.
 */
type RangeValue = '1' | '7' | '30';
const RANGE_OPTIONS: SegmentedToggleOption<RangeValue>[] = [
	{ value: '1', label: '24h' },
	{ value: '7', label: '7d' },
	{ value: '30', label: '30d' },
];
const DEFAULT_RANGE: RangeValue = '7';

const PALETTE = ['#68BAEC', '#5EDEB9', '#FDBD79', '#EDADAF', '#F1E38B', '#8b5cf6'];
const CALLS_COLOR = '#FDBD79';
const TOKENS_COLOR = '#68BAEC';
const AGENTS_COLOR = '#8b5cf6';

/** HTTP method → colour (write/destructive verbs skew warm). */
const METHOD_COLOR: Record<string, string> = {
	GET: '#5EDEB9',
	POST: '#68BAEC',
	PUT: '#FDBD79',
	PATCH: '#F1E38B',
	DELETE: '#EDADAF',
};

interface SessionPoint {
	id: string;
	title: string;
	calls: number;
	tokens: number;
	agents: number;
	startedAt: string | null;
}

/** Which series are currently visible. All true by default. */
interface SeriesVisibility {
	calls: boolean;
	tokens: boolean;
	agents: boolean;
}

/** The colored swatch/line marker shown next to a legend label. */
function LegendMarker({
	color,
	line,
	dashed,
}: {
	color: string;
	line?: boolean;
	dashed?: boolean;
}) {
	return line ? (
		<svg width="16" height="8" aria-hidden="true">
			<line
				x1="0"
				y1="4"
				x2="16"
				y2="4"
				stroke={color}
				strokeWidth="2"
				strokeDasharray={dashed ? '3 2' : undefined}
			/>
			{!dashed && <circle cx="8" cy="4" r="2.4" fill={color} />}
		</svg>
	) : (
		<span
			className="inline-block h-2.5 w-2.5 rounded-[3px]"
			style={{ backgroundColor: color }}
		/>
	);
}

/**
 * A legend entry rendered as an accessible checkbox that toggles a series.
 * The box is an ordinary (neutral, unfilled) checkbox that shows a tick when
 * checked; the colored swatch/line marker beside it stays the colour key.
 */
function LegendCheckbox({
	color,
	label,
	line,
	dashed,
	checked,
	onChange,
}: {
	color: string;
	label: string;
	line?: boolean;
	dashed?: boolean;
	checked: boolean;
	onChange: (checked: boolean) => void;
}) {
	return (
		<label className="text-muted-foreground group flex cursor-pointer items-center gap-1.5 select-none">
			<span className="relative flex h-3 w-3 shrink-0 items-center justify-center">
				<input
					type="checkbox"
					checked={checked}
					onChange={(e) => onChange(e.target.checked)}
					className="border-border peer h-3 w-3 cursor-pointer appearance-none rounded-[3px] border bg-transparent focus-visible:ring-1 focus-visible:ring-offset-1 focus-visible:outline-none"
				/>
				<Check
					className="text-foreground pointer-events-none absolute h-2.5 w-2.5 opacity-0 peer-checked:opacity-100"
					strokeWidth={3}
					aria-hidden="true"
				/>
			</span>
			<LegendMarker color={color} line={line} dashed={dashed} />
			{label}
		</label>
	);
}

/** "Nice" upper bound + step for an axis, given a raw max (1/2/5 × 10ⁿ). */
function niceScale(max: number, ticks = 4): { top: number; step: number } {
	if (max <= 0) return { top: 1, step: 1 };
	const rough = max / ticks;
	const mag = 10 ** Math.floor(Math.log10(rough));
	const norm = rough / mag;
	const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
	return { top: Math.ceil(max / step) * step, step };
}

/** Catmull-Rom → cubic-bézier smoothing so the line reads like AgentsView's. */
function smoothPath(pts: { x: number; y: number }[]): string {
	if (pts.length === 0) return '';
	if (pts.length === 1) return `M ${pts[0].x} ${pts[0].y}`;
	let d = `M ${pts[0].x} ${pts[0].y}`;
	for (let i = 0; i < pts.length - 1; i++) {
		const p0 = pts[i - 1] ?? pts[i];
		const p1 = pts[i];
		const p2 = pts[i + 1];
		const p3 = pts[i + 2] ?? p2;
		const c1x = p1.x + (p2.x - p0.x) / 6;
		const c1y = p1.y + (p2.y - p0.y) / 6;
		const c2x = p2.x - (p3.x - p1.x) / 6;
		const c2y = p2.y - (p3.y - p1.y) / 6;
		d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`;
	}
	return d;
}

// Fixed pixel-space viewBox — avoids preserveAspectRatio="none" text/stroke
// distortion. The SVG scales to container width via width:100% + meet.
const VB_W = 980;
const PLOT_H = 148;
const PAD = { top: 12, right: 48, bottom: 8, left: 34 };
const PLOT_W = VB_W - PAD.left - PAD.right;
const PLOT_BOTTOM = PAD.top + PLOT_H;

/**
 * AgentsView-style combo chart: solid bars (tokens, left axis — the
 * larger-magnitude value) + two smooth lines (tool calls & agents, right
 * counts axis), with gridlines and dual value axes.
 */
function ComboChart({ points, visible }: { points: SessionPoint[]; visible: SeriesVisibility }) {
	const titleId = useId();
	const [hover, setHover] = useState<number | null>(null);

	const n = points.length;
	const rawTokens = Math.max(1, ...points.map((p) => p.tokens));
	// Tool calls and agents are both small integer counts, so they share a
	// single "nice" counts scale on the right axis.
	const rawCounts = Math.max(1, ...points.map((p) => p.calls), ...points.map((p) => p.agents));

	// Left axis carries tokens (the bars); right axis carries the counts (lines).
	const tokensAxis = niceScale(rawTokens);
	const countsAxis = niceScale(rawCounts);

	const band = PLOT_W / n;
	const barW = Math.min(band * 0.44, 46);
	const cx = (i: number) => PAD.left + band * i + band / 2;

	// Bars = tokens on the LEFT (visible, gridline) axis. Tool calls and agents
	// are count-like integers on the RIGHT counts axis.
	const yBar = (v: number) => PLOT_BOTTOM - (v / tokensAxis.top) * PLOT_H;
	const yCalls = (v: number) => PLOT_BOTTOM - (v / countsAxis.top) * PLOT_H;
	const yAgents = (v: number) => PLOT_BOTTOM - (v / countsAxis.top) * PLOT_H;

	const gridVals = useMemo(() => {
		const vals: number[] = [];
		for (let v = 0; v <= tokensAxis.top + 1e-9; v += tokensAxis.step) vals.push(v);
		return vals;
	}, [tokensAxis.top, tokensAxis.step]);

	const callsLine = smoothPath(points.map((p, i) => ({ x: cx(i), y: yCalls(p.calls) })));
	const agentsLine = smoothPath(points.map((p, i) => ({ x: cx(i), y: yAgents(p.agents) })));

	return (
		<div className="mt-2">
			<div className="relative w-full">
				<svg
					aria-labelledby={titleId}
					viewBox={`0 0 ${VB_W} ${PAD.top + PLOT_H + PAD.bottom}`}
					preserveAspectRatio="xMidYMid meet"
					className="h-auto max-h-56 w-full"
					role="img"
				>
					{gridVals.map((v) => {
						const gy = yBar(v);
						return (
							<g key={v}>
								<line
									x1={PAD.left}
									x2={PAD.left + PLOT_W}
									y1={gy}
									y2={gy}
									className="text-border/40"
									stroke="currentColor"
									strokeWidth={1}
								/>
								<text
									x={PAD.left - 6}
									y={gy + 3}
									textAnchor="end"
									className="fill-muted-foreground"
									fontSize={9}
								>
									{formatTokens(v)}
								</text>
							</g>
						);
					})}

					{/* right axis ticks (counts scale) — hidden when both count series are off */}
					{(visible.calls || visible.agents) &&
						[0, 0.5, 1].map((f) => {
							const gy = PLOT_BOTTOM - f * PLOT_H;
							return (
								<text
									key={f}
									x={PAD.left + PLOT_W + 6}
									y={gy + 3}
									textAnchor="start"
									className="fill-muted-foreground"
									fontSize={9}
								>
									{formatCount(countsAxis.top * f)}
								</text>
							);
						})}

					{visible.tokens &&
						points.map((p, i) => {
							const h = PLOT_BOTTOM - yBar(p.tokens);
							const active = hover === null || hover === i;
							return (
								<rect
									key={p.id}
									x={cx(i) - barW / 2}
									y={yBar(p.tokens)}
									width={barW}
									height={Math.max(2, h)}
									rx={3}
									fill={TOKENS_COLOR}
									opacity={active ? 1 : 0.4}
									style={{ transition: 'opacity 150ms' }}
								/>
							);
						})}

					{visible.calls && (
						<path d={callsLine} fill="none" stroke={CALLS_COLOR} strokeWidth={2.25} />
					)}
					{visible.agents && (
						<path
							d={agentsLine}
							fill="none"
							stroke={AGENTS_COLOR}
							strokeWidth={2}
							strokeDasharray="5 3"
						/>
					)}

					{points.map((p, i) => (
						<g key={p.id}>
							{visible.calls && (
								<circle cx={cx(i)} cy={yCalls(p.calls)} r={3} fill={CALLS_COLOR} />
							)}
							{visible.agents && (
								<circle
									cx={cx(i)}
									cy={yAgents(p.agents)}
									r={2.6}
									fill={AGENTS_COLOR}
								/>
							)}
						</g>
					))}

					{points.map((p, i) => (
						<rect
							key={p.id}
							x={PAD.left + band * i}
							y={PAD.top}
							width={band}
							height={PLOT_H}
							fill="transparent"
							onMouseEnter={() => setHover(i)}
							onMouseLeave={() => setHover(null)}
						/>
					))}
				</svg>

				{hover !== null && (
					<div
						className="border-border/40 bg-card/95 pointer-events-none absolute top-1 z-30 -translate-x-1/2 rounded-lg border px-2.5 py-1.5 text-[11px] whitespace-nowrap shadow-lg backdrop-blur-md"
						style={{ left: `${(cx(hover) / VB_W) * 100}%` }}
					>
						<p className="text-foreground max-w-[14rem] truncate font-semibold">
							{points[hover].title}
						</p>
						<p style={{ color: CALLS_COLOR }}>{points[hover].calls} tool calls</p>
						<p style={{ color: TOKENS_COLOR }}>
							{formatTokens(points[hover].tokens)} tokens
						</p>
						<p style={{ color: AGENTS_COLOR }}>{points[hover].agents} agents</p>
					</div>
				)}
			</div>
		</div>
	);
}

/** Horizontal-bar mini chart: total calls grouped by touched API. */
function CallsByApi({ sessions }: { sessions: ProxySession[] }) {
	const rows = useMemo(() => {
		const totals = new Map<string, number>();
		for (const s of sessions) {
			// Attribute a session's calls across the APIs it touched.
			const share = s.apis_touched.length > 0 ? s.tiles.calls / s.apis_touched.length : 0;
			for (const api of s.apis_touched) {
				totals.set(api, (totals.get(api) ?? 0) + share);
			}
		}
		return [...totals.entries()]
			.map(([api, calls], i) => ({
				api,
				calls: Math.round(calls),
				color: PALETTE[i % PALETTE.length],
			}))
			.sort((a, b) => b.calls - a.calls);
	}, [sessions]);
	const max = Math.max(1, ...rows.map((r) => r.calls));
	return (
		<div>
			<p className="text-foreground text-xs font-semibold">Calls by API (approx.)</p>
			<ul className="mt-2 space-y-2">
				{rows.map((r) => (
					<li key={r.api} className="flex flex-col gap-1">
						<div className="flex items-center justify-between gap-2">
							<span className="text-muted-foreground truncate font-mono text-[11px]">
								{r.api}
							</span>
							<span className="text-foreground shrink-0 text-[11px] tabular-nums">
								{formatCount(r.calls)}
							</span>
						</div>
						<div className="bg-muted/50 h-1.5 w-full rounded-full">
							<div
								className="h-full rounded-full transition-all duration-500"
								style={{
									width: `${Math.max(4, (r.calls / max) * 100)}%`,
									backgroundColor: r.color,
								}}
							/>
						</div>
					</li>
				))}
			</ul>
		</div>
	);
}

/** Horizontal-bar mini chart: calls grouped by HTTP method (read vs write mix). */
function CallsByMethod({ methods }: { methods: Record<string, number> }) {
	const rows = useMemo(() => {
		const ORDER = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
		return Object.entries(methods)
			.map(([method, count]) => ({
				method,
				count,
				color: METHOD_COLOR[method] ?? '#94a3b8',
			}))
			.sort((a, b) => {
				const ai = ORDER.indexOf(a.method);
				const bi = ORDER.indexOf(b.method);
				if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
				return b.count - a.count;
			});
	}, [methods]);
	const max = Math.max(1, ...rows.map((r) => r.count));
	return (
		<div>
			<p className="text-foreground text-xs font-semibold">Calls by HTTP method</p>
			<ul className="mt-2 space-y-2">
				{rows.map((r) => (
					<li key={r.method} className="flex flex-col gap-1">
						<div className="flex items-center justify-between gap-2">
							<span className="text-muted-foreground truncate font-mono text-[11px]">
								{r.method}
							</span>
							<span className="text-foreground shrink-0 text-[11px] tabular-nums">
								{formatCount(r.count)}
							</span>
						</div>
						<div className="bg-muted/50 h-1.5 w-full rounded-full">
							<div
								className="h-full rounded-full transition-all duration-500"
								style={{
									width: `${Math.max(4, (r.count / max) * 100)}%`,
									backgroundColor: r.color,
								}}
							/>
						</div>
					</li>
				))}
			</ul>
		</div>
	);
}

export function SessionActivityChart({
	sessions,
	methods = {},
}: {
	sessions: ProxySession[];
	methods?: Record<string, number>;
}) {
	const [showMore, setShowMore] = useState(false);
	const [range, setRange] = useState<RangeValue>(DEFAULT_RANGE);
	const [visible, setVisible] = useState<SeriesVisibility>({
		calls: true,
		tokens: true,
		agents: true,
	});
	const toggle = (key: keyof SeriesVisibility) => (checked: boolean) =>
		setVisible((v) => ({ ...v, [key]: checked }));
	const points = useMemo<SessionPoint[]>(() => {
		const mapped = sessions
			.map((s) => ({
				id: s.id,
				title: s.title,
				calls: s.tiles.calls,
				tokens: s.tiles.tokens,
				agents: s.tiles.agents,
				startedAt: s.started_at,
			}))
			// Chronological: earliest-started session leftmost. Sessions
			// missing a start time sink to the right (ISO strings sort
			// lexicographically, so localeCompare is sufficient).
			.sort((a, b) => (a.startedAt ?? '\uffff').localeCompare(b.startedAt ?? '\uffff'));

		// The time window is measured relative to the MOST RECENT session's
		// `started_at`, not wall-clock now. Mock sessions aren't anchored to the
		// real current date, so a naive "relative to now" filter could blank the
		// chart for short windows; anchoring to the latest session keeps 24h/7d/
		// 30d visibly filtering the demo data. Sessions without a start time are
		// always kept so the chart never mistakenly drops them.
		const times = mapped
			.map((p) => (p.startedAt ? Date.parse(p.startedAt) : NaN))
			.filter((t) => !Number.isNaN(t));
		if (times.length === 0) return mapped;
		const anchor = Math.max(...times);
		const cutoff = anchor - Number(range) * 24 * 60 * 60 * 1000;
		return mapped.filter((p) => {
			if (!p.startedAt) return true;
			const t = Date.parse(p.startedAt);
			return Number.isNaN(t) || t >= cutoff;
		});
	}, [sessions, range]);

	return (
		<Card>
			<CardHeader className="flex flex-wrap items-start justify-between gap-2">
				<div>
					<CardTitle className="text-sm">Per-session activity</CardTitle>
					<p className="text-muted-foreground text-xs">
						Tool calls, tokens consumed, and agents spawned — compared across sessions
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-3 text-xs">
					<div className="flex items-center gap-3">
						<LegendCheckbox
							color={CALLS_COLOR}
							label="Tool calls"
							line
							checked={visible.calls}
							onChange={toggle('calls')}
						/>
						<LegendCheckbox
							color={TOKENS_COLOR}
							label="Tokens"
							checked={visible.tokens}
							onChange={toggle('tokens')}
						/>
						<LegendCheckbox
							color={AGENTS_COLOR}
							label="Agents"
							line
							dashed
							checked={visible.agents}
							onChange={toggle('agents')}
						/>
					</div>
					<SegmentedToggle
						options={RANGE_OPTIONS}
						value={range}
						onChange={setRange}
						ariaLabel="Session time window"
					/>
				</div>
			</CardHeader>
			<CardBody>
				{points.length === 0 ? (
					<p className="text-muted-foreground py-12 text-center text-sm">
						No session data available
					</p>
				) : (
					<ComboChart points={points} visible={visible} />
				)}

				<div className="mt-4 flex justify-center">
					<Button
						variant="ghost"
						size="sm"
						onClick={() => setShowMore((v) => !v)}
						aria-expanded={showMore}
						className="text-muted-foreground hover:text-foreground gap-1.5"
					>
						<BarChart3 className="h-3.5 w-3.5" />
						{showMore ? 'Hide charts' : 'See more charts'}
						<ChevronDown
							className={`h-3.5 w-3.5 transition-transform ${showMore ? 'rotate-180' : ''}`}
						/>
					</Button>
				</div>

				<AnimatePresence initial={false}>
					{showMore && (
						<motion.div
							initial={{ height: 0, opacity: 0 }}
							animate={{ height: 'auto', opacity: 1 }}
							exit={{ height: 0, opacity: 0 }}
							transition={{ duration: 0.25, ease: 'easeInOut' }}
							className="overflow-hidden"
						>
							<div className="border-border/50 mt-2 grid grid-cols-1 gap-6 border-t pt-4 md:grid-cols-2">
								<CallsByApi sessions={sessions} />
								<CallsByMethod methods={methods} />
							</div>
						</motion.div>
					)}
				</AnimatePresence>
			</CardBody>
		</Card>
	);
}
