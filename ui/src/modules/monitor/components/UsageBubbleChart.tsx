/**
 * UsageBubbleChart — ported from jentic-mini's `ApiBubbleChart`.
 *
 * One bubble per api / toolkit / agent: bubble area encodes execution volume,
 * the partial ring around it encodes success rate, and hovering surfaces a
 * calls / success / latency tooltip. The segmented toggle flips between the
 * three lenses without refetching (the Overview pre-fetches all three
 * groupings).
 *
 * Differences from the mini original: no vendor-icon registry in jentic-one,
 * so every bubble renders an initials tile from a stable index palette, and
 * the tooltip drops the cross-entity "Used by" / "Top APIs" sections (the
 * usage endpoint doesn't expose per-toolkit top-API relations).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/shared/lib/utils';
import { SegmentedToggle } from '@/shared/ui';
import { formatLatency, formatPercent } from '@/modules/monitor/lib/format';
import {
	getInitials,
	lensPalette,
	ringColor,
	textColor,
	type UsageLens,
} from '@/modules/monitor/lib/palette';
import type { EntityUsageRow } from '@/modules/monitor/lib/usage';

interface UsageBubbleChartProps {
	apis: EntityUsageRow[];
	toolkits: EntityUsageRow[];
	agents: EntityUsageRow[];
	className?: string;
}

interface BubbleNode {
	item: EntityUsageRow;
	x: number;
	y: number;
	radius: number;
	color: string;
	ringColor: string;
}

const LENS_TITLES: Record<UsageLens, string> = {
	apis: 'API Usage',
	toolkits: 'Toolkit Activity',
	agents: 'Agent Activity',
};

const LENS_NOUNS: Record<UsageLens, string> = {
	apis: 'API',
	toolkits: 'toolkit',
	agents: 'agent',
};

/**
 * Greedy circle packing (verbatim from jentic-mini): place biggest first at
 * the centre, then spiral each next bubble outward to the closest free spot.
 */
function packCircles(
	nodes: Array<Omit<BubbleNode, 'x' | 'y'>>,
	width: number,
	height: number,
): BubbleNode[] {
	const cx = width / 2;
	const cy = height / 2;
	const pad = 8;

	const sorted = [...nodes].sort((a, b) => b.radius - a.radius);
	const placed: BubbleNode[] = [];

	for (const node of sorted) {
		if (placed.length === 0) {
			placed.push({ ...node, x: cx, y: cy });
			continue;
		}

		let bestX = cx;
		let bestY = cy;
		let bestDist = Infinity;

		for (let angle = 0; angle < Math.PI * 2; angle += 0.15) {
			for (let dist = 0; dist < Math.max(width, height); dist += 3) {
				const testX = cx + Math.cos(angle) * dist;
				const testY = cy + Math.sin(angle) * dist;

				const r = node.radius + 4;
				if (
					testX - r < pad ||
					testX + r > width - pad ||
					testY - r < pad ||
					testY + r > height - pad
				) {
					continue;
				}

				let overlaps = false;
				for (const p of placed) {
					const dx = testX - p.x;
					const dy = testY - p.y;
					const gap = 6;
					if (Math.sqrt(dx * dx + dy * dy) < node.radius + p.radius + gap) {
						overlaps = true;
						break;
					}
				}

				if (!overlaps) {
					const distFromCenter = Math.sqrt((testX - cx) ** 2 + (testY - cy) ** 2);
					if (distFromCenter < bestDist) {
						bestDist = distFromCenter;
						bestX = testX;
						bestY = testY;
					}
					break;
				}
			}
		}

		placed.push({ ...node, x: bestX, y: bestY });
	}

	return placed;
}

export function UsageBubbleChart({ apis, toolkits, agents, className }: UsageBubbleChartProps) {
	const containerRef = useRef<HTMLDivElement>(null);
	const [dimensions, setDimensions] = useState({ width: 600, height: 420 });
	// Store only the hovered entity id, not the node: background refetches
	// rebuild `bubbles`, so a captured node would pin the tooltip to stale
	// counts/position — or leave it stuck if the entity drops out of the top
	// rows (the <g> unmounts without firing mouseleave).
	const [hoveredId, setHoveredId] = useState<string | null>(null);
	const [lens, setLens] = useState<UsageLens>('apis');

	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const { width } = entry.contentRect;
				if (width === 0) continue;
				const height = Math.max(340, Math.min(width * 0.7, 480));
				// Bail on no-op ticks so a window drag doesn't re-run the circle
				// packer once per observer callback.
				setDimensions((prev) =>
					prev.width === width && prev.height === height ? prev : { width, height },
				);
			}
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, []);

	useEffect(() => {
		setHoveredId(null);
	}, [lens]);

	const items = lens === 'apis' ? apis : lens === 'toolkits' ? toolkits : agents;

	const bubbles = useMemo(() => {
		if (items.length === 0) return [];

		const palette = lensPalette(lens);
		const maxExec = Math.max(1, ...items.map((a) => a.totalExecutions));
		const minRadius = 28;
		const maxRadius = Math.min(dimensions.width, dimensions.height) * 0.18;

		const nodes = items.map((item, i) => {
			const color = palette[i % palette.length];
			return {
				item,
				radius:
					minRadius + (item.totalExecutions / maxExec) ** 0.6 * (maxRadius - minRadius),
				color,
				ringColor: ringColor(color),
			};
		});

		return packCircles(nodes, dimensions.width, dimensions.height);
	}, [items, dimensions, lens]);

	// Resolve the hovered node from the *current* pack so the tooltip always
	// reflects live data (and vanishes if the entity left the top rows).
	const hovered = hoveredId ? (bubbles.find((b) => b.item.id === hoveredId) ?? null) : null;

	const handleMouseEnter = useCallback((bubble: BubbleNode) => setHoveredId(bubble.item.id), []);
	const handleMouseLeave = useCallback(() => setHoveredId(null), []);

	return (
		<div
			ref={containerRef}
			className={cn(
				'border-border bg-card relative min-w-0 overflow-hidden rounded-xl border',
				className,
			)}
		>
			<div className="flex items-start justify-between gap-2 px-4 pt-3 pb-0">
				<div>
					<h2 className="text-foreground text-sm font-semibold">{LENS_TITLES[lens]}</h2>
					<p className="text-muted-foreground text-xs">
						Bubble size = execution volume, ring = success rate
					</p>
				</div>

				<SegmentedToggle
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={lens}
					onChange={setLens}
					ariaLabel="Bubble chart grouping"
				/>
			</div>

			{items.length === 0 ? (
				<div className="flex items-center justify-center py-24">
					<p className="text-muted-foreground text-sm">
						No {LENS_NOUNS[lens]} data available
					</p>
				</div>
			) : (
				<AnimatePresence mode="wait">
					<motion.div
						key={lens}
						initial={{ opacity: 0, scale: 0.97 }}
						animate={{ opacity: 1, scale: 1 }}
						exit={{ opacity: 0, scale: 0.97 }}
						transition={{ duration: 0.25 }}
					>
						<svg
							width={dimensions.width}
							height={dimensions.height}
							viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
							className="overflow-hidden"
							role="img"
							aria-label={`${LENS_TITLES[lens]} bubble chart`}
						>
							<defs>
								<filter
									id="bubble-shadow"
									x="-20%"
									y="-20%"
									width="140%"
									height="140%"
								>
									<feDropShadow
										dx="0"
										dy="2"
										stdDeviation="4"
										floodOpacity="0.15"
									/>
								</filter>
								<filter
									id="bubble-glow"
									x="-30%"
									y="-30%"
									width="160%"
									height="160%"
								>
									<feDropShadow
										dx="0"
										dy="0"
										stdDeviation="8"
										floodOpacity="0.3"
									/>
								</filter>
								<radialGradient id="bubble-shine" cx="35%" cy="35%" r="65%">
									<stop offset="0%" stopColor="white" stopOpacity="0.4" />
									<stop offset="100%" stopColor="white" stopOpacity="0" />
								</radialGradient>
							</defs>

							{bubbles.map((bubble) => {
								const isHovered = hovered?.item.id === bubble.item.id;
								const isDimmed = hovered && !isHovered;

								const successPct = bubble.item.successRate / 100;
								const ringRadius = bubble.radius + 3;
								const circumference = 2 * Math.PI * ringRadius;
								const successStroke = circumference * successPct;
								const failStroke = circumference * (1 - successPct);

								return (
									<g
										key={bubble.item.id}
										onMouseEnter={() => handleMouseEnter(bubble)}
										onMouseLeave={handleMouseLeave}
									>
										{/* Oversized invisible hit target so the tooltip doesn't
										    flicker at the bubble edge. */}
										<circle
											cx={bubble.x}
											cy={bubble.y}
											r={bubble.radius + 12}
											fill="transparent"
										/>

										<g
											className="transition-all duration-200"
											style={{
												opacity: isDimmed ? 0.35 : 1,
												transform: isHovered ? 'scale(1.08)' : 'scale(1)',
												transformOrigin: `${bubble.x}px ${bubble.y}px`,
											}}
										>
											<circle
												cx={bubble.x}
												cy={bubble.y}
												r={ringRadius}
												fill="none"
												stroke="currentColor"
												strokeWidth={2.5}
												className="text-accent-pink/30"
											/>
											<circle
												cx={bubble.x}
												cy={bubble.y}
												r={ringRadius}
												fill="none"
												stroke={bubble.ringColor}
												strokeWidth={2.5}
												strokeDasharray={`${successStroke} ${failStroke}`}
												strokeDashoffset={circumference * 0.25}
												strokeLinecap="round"
												className="transition-all duration-500"
											/>

											<circle
												cx={bubble.x}
												cy={bubble.y}
												r={bubble.radius}
												fill={bubble.color}
												filter={
													isHovered
														? 'url(#bubble-glow)'
														: 'url(#bubble-shadow)'
												}
												className="transition-all duration-200"
											/>
											<circle
												cx={bubble.x}
												cy={bubble.y}
												r={bubble.radius}
												fill="url(#bubble-shine)"
												opacity={0.15}
											/>

											<text
												x={bubble.x}
												y={bubble.y - (bubble.radius > 40 ? 6 : 0)}
												textAnchor="middle"
												dominantBaseline="central"
												fill={textColor(bubble.color)}
												fontSize={
													bubble.radius > 50
														? 14
														: bubble.radius > 35
															? 11
															: 9
												}
												fontWeight={700}
												className="pointer-events-none select-none"
												style={{
													fontFamily: 'var(--font-sans, system-ui)',
												}}
											>
												{getInitials(bubble.item.label)}
											</text>
											{bubble.radius > 40 && (
												<text
													x={bubble.x}
													y={bubble.y + (bubble.radius > 50 ? 12 : 8)}
													textAnchor="middle"
													dominantBaseline="central"
													fill={textColor(bubble.color)}
													fontSize={bubble.radius > 50 ? 9 : 8}
													opacity={0.7}
													className="pointer-events-none select-none"
													style={{
														fontFamily: 'var(--font-sans, system-ui)',
													}}
												>
													{bubble.item.totalExecutions.toLocaleString()}
												</text>
											)}
										</g>
									</g>
								);
							})}
						</svg>
					</motion.div>
				</AnimatePresence>
			)}

			{hovered && (
				<BubbleTooltip
					item={hovered.item}
					x={hovered.x}
					y={hovered.y}
					containerWidth={dimensions.width}
				/>
			)}
		</div>
	);
}

function BubbleTooltip({
	item,
	x,
	y,
	containerWidth,
}: {
	item: EntityUsageRow;
	x: number;
	y: number;
	containerWidth: number;
}) {
	const isRight = x > containerWidth / 2;

	return (
		<div
			className="border-border bg-card pointer-events-none absolute z-20 w-56 rounded-lg border p-3 shadow-xl"
			style={{ left: isRight ? x - 240 : x + 20, top: Math.max(8, y - 60) }}
		>
			<p className="text-foreground mb-2 text-sm font-medium">{item.label}</p>

			<div className="grid grid-cols-3 gap-2 text-center">
				<div>
					<p className="text-foreground text-base font-semibold">
						{item.totalExecutions.toLocaleString()}
					</p>
					<p className="text-muted-foreground text-[10px]">calls</p>
				</div>
				<div>
					<p className="text-foreground text-base font-semibold">
						{formatPercent(item.successRate)}
					</p>
					<p className="text-muted-foreground text-[10px]">success</p>
				</div>
				<div>
					<p className="text-foreground text-base font-semibold">
						{formatLatency(item.avgLatencyMs)}
					</p>
					<p className="text-muted-foreground text-[10px]">latency</p>
				</div>
			</div>
		</div>
	);
}
