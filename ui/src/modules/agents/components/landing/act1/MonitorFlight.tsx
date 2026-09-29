/**
 * MonitorFlight — the hand-off from the lane to the Monitor. When an event
 * lands in the Monitor, the call's own badge (its API mark and step number, a
 * bell for "needs you", a ban for a denied call, a pause for the agent) lifts
 * off where it happened — the API box the call ended at, or for agent events
 * the lane's right edge beside the API column — and flies along a faint, short
 * bridge that lives only in the gutter between the lane and the Monitor, into
 * the new Monitor item, where it hands off to that item's own leading badge.
 *
 * The Monitor item stays hidden while the badge is in flight and appears the
 * moment it lands (the parent passes `landing` to the operator panel, which
 * also shows the short label there). Nothing floats over the diagram, and
 * nothing here is announced: the act's narration caption says the same.
 *
 * Positions are measured from `data-with-api` / `with-agent` (lane) and
 * `data-monitor-anchor` (Monitor) elements inside `containerRef`, re-measured on
 * resize. The overlay is `pointer-events: none`; motion is transform, opacity
 * and pathLength only. Reduced motion: no flight and no bridge — the item and
 * its label simply appear, highlighted.
 */
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { motion } from 'framer-motion';
import { Ban, Bell, Check, Pause } from 'lucide-react';
import { VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { MonitorCallout } from '@/modules/agents/components/landing/act1/model';
import {
	EASE_OUT_SOFT,
	cubicPath,
	cubicSamples,
	type Cubic,
	type Pt,
} from '@/modules/agents/components/landing/motion';

/** Timings, in ms from the event. The badge lands when the item appears. */
const DRAW_MS = 300;
const LIFT_MS = 150;
const FLY_MS = 900;
const LAND_MS = DRAW_MS + LIFT_MS + FLY_MS;
const BRIDGE_FADE_MS = 400;
/** How long the label stays in the Monitor after landing. */
const LABEL_MS = 2500;

export type FlightPhase = 'flying' | 'landed';

export interface MonitorLanding {
	key: string;
	to: MonitorCallout['to'];
	kind: MonitorCallout['kind'];
	text: string;
	phase: FlightPhase;
}

interface Flight {
	/** The bridge: a cubic S-curve across the gutter, in container coordinates. */
	bridge: Cubic;
	/** The target item's leading badge centre (the last few px, inside the row). */
	land: Pt;
	size: { w: number; h: number };
}

/** The newest Monitor item for `to`, and the leading badge inside it. */
function monitorItem(
	root: ParentNode,
	to: MonitorCallout['to'],
): { item: HTMLElement; badge: HTMLElement } | null {
	const anchor = root.querySelector<HTMLElement>(`[data-monitor-anchor="${to}"]`);
	if (!anchor) return null;
	const item = to === 'agent' ? anchor : anchor.querySelector<HTMLElement>('li');
	if (!item) return null;
	const badge = (item.firstElementChild as HTMLElement | null) ?? item;
	return { item, badge };
}

function measure(root: HTMLElement, callout: MonitorCallout): Flight | null {
	const target = monitorItem(root, callout.to);
	const lane = root.querySelector<HTMLElement>('[data-testid="with-lane"]');
	if (!target || !lane) return null;
	const base = root.getBoundingClientRect();
	const apis = [...lane.querySelectorAll<SVGGElement>('[data-with-api]')];
	if (apis.length === 0) return null;
	const columnRight = Math.max(...apis.map((a) => a.getBoundingClientRect().right));
	let from: Pt;
	if (callout.from === 'agent') {
		// Agent events leave from the lane's right edge, level with the agent.
		const agent = lane.querySelector('[data-testid="with-agent"]')?.getBoundingClientRect();
		if (!agent) return null;
		from = { x: columnRight + 2, y: agent.top + agent.height / 2 };
	} else {
		const box = lane
			.querySelector(`[data-with-api="${callout.from}"]`)
			?.getBoundingClientRect();
		if (!box) return null;
		from = { x: box.right + 2, y: box.top + box.height / 2 };
	}
	const item = target.item.getBoundingClientRect();
	const badge = target.badge.getBoundingClientRect();
	// Side by side only: a stacked (narrow) layout has no gutter to bridge.
	if (item.left <= columnRight + 8) return null;
	const landY = badge.top + badge.height / 2;
	const to: Pt = { x: item.left - 2, y: landY };
	const rel = (p: Pt): Pt => ({ x: p.x - base.left, y: p.y - base.top });
	const a = rel(from);
	const d = rel(to);
	const mid = (a.x + d.x) / 2;
	return {
		bridge: [a, { x: mid, y: a.y }, { x: mid, y: d.y }, d],
		land: rel({ x: badge.left + badge.width / 2, y: landY }),
		size: { w: base.width, h: base.height },
	};
}

/** Points along the bridge, then into the item's badge: the badge's flight. */
function flightKeyframes(f: Flight): { x: number[]; y: number[] } {
	const { x, y } = cubicSamples(f.bridge, 16);
	return { x: [...x, f.land.x], y: [...y, f.land.y] };
}

/** The badge that flies: the same one the call chip / Monitor row shows. */
function FlightBadge({ callout }: { callout: MonitorCallout }) {
	const api = callout.from === 'agent' ? null : callout.from;
	const Icon =
		callout.kind === 'needs'
			? Bell
			: callout.kind === 'denied'
				? Ban
				: callout.kind === 'paused'
					? Pause
					: null;
	const tone =
		callout.kind === 'denied'
			? 'border-danger/60 text-danger'
			: callout.kind === 'recorded'
				? 'border-primary/50 text-primary'
				: 'border-warning/60 text-warning';
	return (
		<span
			className={cn(
				'bg-card inline-flex h-6 items-center gap-1 rounded-full border px-1.5 shadow-md',
				tone,
			)}
		>
			{api && <VendorMark slug={api} size="xs" />}
			{Icon ? (
				<Icon className="h-3.5 w-3.5" aria-hidden="true" />
			) : callout.tag ? (
				<span className="bg-primary text-primary-foreground inline-flex h-4 w-4 items-center justify-center rounded-full font-mono text-[9px] font-bold">
					{callout.tag}
				</span>
			) : (
				<Check className="h-3.5 w-3.5" aria-hidden="true" />
			)}
		</span>
	);
}

export function MonitorFlight({
	callout,
	beatKey,
	containerRef,
	reduced,
	onLanding,
}: {
	callout: MonitorCallout | null;
	/** Changes on every beat: a flight starts only when its beat starts. */
	beatKey: string | undefined;
	containerRef: RefObject<HTMLElement | null>;
	reduced: boolean;
	/** The Monitor item's state: hidden while flying, shown (and labelled) once landed. */
	onLanding: (landing: MonitorLanding | null) => void;
}) {
	const [active, setActive] = useState<{ key: string; callout: MonitorCallout } | null>(null);
	const [flight, setFlight] = useState<Flight | null>(null);
	const [flying, setFlying] = useState(false);
	// A stable handle on the latest callback, so the clock below never restarts for it.
	const report = useRef(onLanding);
	useLayoutEffect(() => {
		report.current = onLanding;
	});
	// `useId` may contain characters a `url(#…)` reference can't carry.
	const gradientId = `bridge-${useId().replace(/[^\w-]/g, '')}`;

	// The hand-off's clock: fly, land (the item appears), label, done.
	useEffect(() => {
		if (!callout || !beatKey) {
			setActive(null);
			report.current(null);
			return undefined;
		}
		setActive({ key: beatKey, callout });
		const land = (phase: FlightPhase) =>
			report.current({
				key: beatKey,
				to: callout.to,
				kind: callout.kind,
				text: callout.text,
				phase,
			});
		if (reduced) {
			setFlying(false);
			land('landed');
			return undefined;
		}
		setFlying(true);
		land('flying');
		const timers = [
			window.setTimeout(() => land('landed'), LAND_MS),
			window.setTimeout(() => setFlying(false), LAND_MS + BRIDGE_FADE_MS),
			window.setTimeout(() => report.current(null), LAND_MS + LABEL_MS),
		];
		return () => timers.forEach((t) => window.clearTimeout(t));
	}, [callout, beatKey, reduced]);

	useLayoutEffect(() => {
		const root = containerRef.current;
		if (!active || !root || reduced) {
			setFlight(null);
			return undefined;
		}
		const update = () => setFlight(measure(root, active.callout));
		update();
		// Re-measure once the new item has laid out, and on any resize.
		const raf = requestAnimationFrame(update);
		const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
		ro?.observe(root);
		return () => {
			cancelAnimationFrame(raf);
			ro?.disconnect();
		};
	}, [active, containerRef, reduced]);

	const show = active && flight && flying && !reduced;
	return (
		<>
			{show && (
				<div
					key={active.key}
					aria-hidden="true"
					data-testid="monitor-flight"
					className="pointer-events-none absolute inset-0 z-20"
				>
					<svg
						className="text-primary pointer-events-none absolute inset-0 overflow-visible"
						width={flight.size.w}
						height={flight.size.h}
						viewBox={`0 0 ${flight.size.w} ${flight.size.h}`}
					>
						<defs>
							<linearGradient
								id={gradientId}
								gradientUnits="userSpaceOnUse"
								x1={flight.bridge[0].x}
								y1={0}
								x2={flight.bridge[3].x}
								y2={0}
							>
								<stop offset="0" stopColor="currentColor" stopOpacity="0.1" />
								<stop offset="0.5" stopColor="currentColor" stopOpacity="0.45" />
								<stop offset="1" stopColor="currentColor" stopOpacity="0.15" />
							</linearGradient>
						</defs>
						<motion.path
							data-testid="monitor-bridge"
							d={cubicPath(flight.bridge)}
							fill="none"
							stroke={`url(#${gradientId})`}
							strokeWidth={1}
							strokeLinecap="round"
							initial={{ pathLength: 0, opacity: 1 }}
							animate={{ pathLength: 1, opacity: [1, 1, 0] }}
							transition={{
								pathLength: { duration: DRAW_MS / 1000, ease: EASE_OUT_SOFT },
								opacity: {
									duration: (LAND_MS + BRIDGE_FADE_MS) / 1000,
									times: [0, LAND_MS / (LAND_MS + BRIDGE_FADE_MS), 1],
								},
							}}
						/>
					</svg>
					{/* The badge: lifts off, follows the bridge, hands off to the item. */}
					<motion.div
						data-testid="monitor-flight-badge"
						data-land-x={Math.round(flight.land.x)}
						data-land-y={Math.round(flight.land.y)}
						className="absolute top-0 left-0"
						initial={{
							x: flight.bridge[0].x,
							y: flight.bridge[0].y,
							opacity: 0,
							scale: 0.9,
						}}
						animate={{
							...flightKeyframes(flight),
							opacity: [0, 1, 1, 0],
							scale: [0.9, 1.15, 1, 0.85],
						}}
						transition={{
							delay: (DRAW_MS - 50) / 1000,
							x: { duration: (LIFT_MS + FLY_MS) / 1000, ease: EASE_OUT_SOFT },
							y: { duration: (LIFT_MS + FLY_MS) / 1000, ease: EASE_OUT_SOFT },
							opacity: {
								duration: (LIFT_MS + FLY_MS) / 1000,
								times: [0, 0.12, 0.88, 1],
							},
							scale: {
								duration: (LIFT_MS + FLY_MS) / 1000,
								times: [0, 0.14, 0.8, 1],
							},
						}}
					>
						<span className="block -translate-x-1/2 -translate-y-1/2">
							<FlightBadge callout={active.callout} />
						</span>
					</motion.div>
				</div>
			)}
		</>
	);
}
