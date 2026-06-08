import { type JSX, useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type {
	AgentUsageSummary,
	ApiUsageSummary,
	ToolkitUsageSummary,
} from '@/components/monitor/types';
import {
	getVendorConfig,
	getInitials,
	getIconFilterId,
	ICON_INVERT_FILTER_ID,
	ICON_DARK_FILTER_ID,
} from '@/components/monitor/shared/vendor-icons';
import { formatLatency, formatPercent } from '@/components/monitor/shared';
import { SegmentedToggle } from '@/components/ui/SegmentedToggle';
import { cn } from '@/lib/utils';

type BubbleMode = 'apis' | 'toolkits' | 'agents';

interface ApiBubbleChartProps {
	apis: ApiUsageSummary[];
	toolkits: ToolkitUsageSummary[];
	agents: AgentUsageSummary[];
	className?: string;
}

interface BubbleItem {
	id: string;
	label: string;
	subLabel?: string;
	totalExecutions: number;
	successRate: number;
	avgLatencyMs: number;
	vendor?: string;
	apiName?: string;
	toolkitId?: string;
	agentId?: string;
	topApis?: Array<{ vendor: string; apiName: string; count: number }>;
}

interface BubbleNode {
	item: BubbleItem;
	x: number;
	y: number;
	radius: number;
	color: string;
	ringColor: string;
	textColor: string;
	iconUrl?: string;
}

const TOOLKIT_COLORS = [
	{ bg: '#6366f1', ring: '#818cf8', text: '#fff' },
	{ bg: '#8b5cf6', ring: '#a78bfa', text: '#fff' },
	{ bg: '#0ea5e9', ring: '#38bdf8', text: '#fff' },
	{ bg: '#14b8a6', ring: '#2dd4bf', text: '#fff' },
	{ bg: '#f59e0b', ring: '#fbbf24', text: '#1a1a1a' },
	{ bg: '#ef4444', ring: '#f87171', text: '#fff' },
	{ bg: '#ec4899', ring: '#f472b6', text: '#fff' },
	{ bg: '#10b981', ring: '#34d399', text: '#fff' },
];

const AGENT_COLORS = [
	{ bg: '#0891b2', ring: '#22d3ee', text: '#fff' },
	{ bg: '#7c3aed', ring: '#a78bfa', text: '#fff' },
	{ bg: '#db2777', ring: '#f472b6', text: '#fff' },
	{ bg: '#16a34a', ring: '#4ade80', text: '#fff' },
	{ bg: '#ea580c', ring: '#fb923c', text: '#fff' },
	{ bg: '#475569', ring: '#94a3b8', text: '#fff' },
];

function getToolkitColor(index: number) {
	return TOOLKIT_COLORS[index % TOOLKIT_COLORS.length];
}

function getAgentColor(index: number) {
	return AGENT_COLORS[index % AGENT_COLORS.length];
}

function packCircles(
	nodes: Array<{
		item: BubbleItem;
		radius: number;
		color: string;
		ringColor: string;
		textColor: string;
		iconUrl?: string;
	}>,
	width: number,
	height: number,
): BubbleNode[] {
	const cx = width / 2;
	const cy = height / 2;
	const pad = 8;

	const sorted = [...nodes].sort((a, b) => b.radius - a.radius);
	const placed: BubbleNode[] = [];

	for (const node of sorted) {
		let bestX = cx;
		let bestY = cy;

		if (placed.length === 0) {
			placed.push({
				item: node.item,
				x: cx,
				y: cy,
				radius: node.radius,
				color: node.color,
				ringColor: node.ringColor,
				textColor: node.textColor,
				iconUrl: node.iconUrl,
			});
			continue;
		}

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

		placed.push({
			item: node.item,
			x: bestX,
			y: bestY,
			radius: node.radius,
			color: node.color,
			ringColor: node.ringColor,
			textColor: node.textColor,
			iconUrl: node.iconUrl,
		});
	}

	return placed;
}

interface TooltipData {
	item: BubbleItem;
	x: number;
	y: number;
}

export function ApiBubbleChart({
	apis,
	toolkits,
	agents,
	className,
}: ApiBubbleChartProps): JSX.Element {
	const containerRef = useRef<HTMLDivElement>(null);
	const [dimensions, setDimensions] = useState({ width: 600, height: 420 });
	const [hovered, setHovered] = useState<TooltipData | null>(null);
	const [mode, setMode] = useState<BubbleMode>('apis');

	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) {
				const { width } = entry.contentRect;
				setDimensions({ width, height: Math.max(340, Math.min(width * 0.7, 480)) });
			}
		});
		observer.observe(el);
		return () => observer.disconnect();
	}, []);

	useEffect(() => {
		setHovered(null);
	}, [mode]);

	const items: BubbleItem[] = useMemo(() => {
		if (mode === 'apis') {
			return apis.map((api) => ({
				id: api.vendor + ':' + api.apiName,
				label: api.apiName,
				subLabel: api.apiVersion,
				totalExecutions: api.totalExecutions,
				successRate: api.successRate,
				avgLatencyMs: api.avgLatencyMs,
				vendor: api.vendor,
				apiName: api.apiName,
			}));
		}
		if (mode === 'agents') {
			return agents.map((agent) => ({
				id: agent.agentId,
				label: agent.agentName,
				totalExecutions: agent.totalExecutions,
				successRate: agent.successRate,
				avgLatencyMs: agent.avgLatencyMs,
				agentId: agent.agentId,
			}));
		}
		return toolkits.map((toolkit) => ({
			id: toolkit.toolkitId,
			label: toolkit.toolkitName,
			subLabel: toolkit.toolkitMode ?? undefined,
			totalExecutions: toolkit.totalExecutions,
			successRate: toolkit.successRate,
			avgLatencyMs: toolkit.avgLatencyMs,
			toolkitId: toolkit.toolkitId,
			topApis: toolkit.topApis,
		}));
	}, [mode, apis, toolkits, agents]);

	const bubbles = useMemo(() => {
		if (items.length === 0) return [];

		const maxExec = Math.max(...items.map((a) => a.totalExecutions));
		const minRadius = 28;
		const maxRadius = Math.min(dimensions.width, dimensions.height) * 0.18;

		const nodes = items.map((item, i) => {
			if (mode === 'apis' && item.vendor) {
				const cfg = getVendorConfig(item.vendor);
				return {
					item,
					radius:
						minRadius +
						(item.totalExecutions / maxExec) ** 0.6 * (maxRadius - minRadius),
					color: cfg.bg,
					ringColor: cfg.ring,
					textColor: cfg.text,
					iconUrl: cfg.iconUrl,
				};
			}
			if (mode === 'agents') {
				const palette = getAgentColor(i);
				return {
					item,
					radius:
						minRadius +
						(item.totalExecutions / maxExec) ** 0.6 * (maxRadius - minRadius),
					color: palette.bg,
					ringColor: palette.ring,
					textColor: palette.text,
				};
			}
			const toolkitColor = getToolkitColor(i);
			return {
				item,
				radius:
					minRadius + (item.totalExecutions / maxExec) ** 0.6 * (maxRadius - minRadius),
				color: toolkitColor.bg,
				ringColor: toolkitColor.ring,
				textColor: toolkitColor.text,
			};
		});

		return packCircles(nodes, dimensions.width, dimensions.height);
	}, [items, dimensions, mode]);

	const handleMouseEnter = useCallback((bubble: BubbleNode) => {
		setHovered({ item: bubble.item, x: bubble.x, y: bubble.y });
	}, []);

	const handleMouseLeave = useCallback(() => {
		setHovered(null);
	}, []);

	const isEmpty =
		(mode === 'apis' && apis.length === 0) ||
		(mode === 'toolkits' && toolkits.length === 0) ||
		(mode === 'agents' && agents.length === 0);

	return (
		<div
			ref={containerRef}
			className={cn(
				'border-border bg-card relative min-w-0 overflow-hidden rounded-xl border',
				className,
			)}
		>
			<div className="flex items-start justify-between px-4 pt-3 pb-0">
				<div>
					<h3 className="text-foreground text-sm font-medium">
						{mode === 'apis'
							? 'API Usage'
							: mode === 'agents'
								? 'Agent Activity'
								: 'Toolkit Activity'}
					</h3>
					<p className="text-muted-foreground text-xs">
						Bubble size = execution volume, ring = success rate
					</p>
				</div>

				<SegmentedToggle
					layoutId="bubbleToggle"
					options={[
						{ value: 'apis', label: 'APIs' },
						{ value: 'toolkits', label: 'Toolkits' },
						{ value: 'agents', label: 'Agents' },
					]}
					value={mode}
					onChange={setMode}
				/>
			</div>

			{isEmpty ? (
				<div className="flex items-center justify-center py-24">
					<p className="text-muted-foreground text-sm">
						No {mode === 'apis' ? 'API' : mode === 'agents' ? 'agent' : 'toolkit'} data
						available
					</p>
				</div>
			) : (
				<AnimatePresence mode="wait">
					<motion.div
						key={mode}
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
						>
							<defs>
								{bubbles.map((bubble) => (
									<clipPath
										key={`clip-${bubble.item.id}`}
										id={`clip-${bubble.item.id}`}
									>
										<circle cx={bubble.x} cy={bubble.y} r={bubble.radius - 2} />
									</clipPath>
								))}

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

								<filter id={`bb-${ICON_INVERT_FILTER_ID}`}>
									<feColorMatrix
										type="matrix"
										values="-1 0 0 0 1  0 -1 0 0 1  0 0 -1 0 1  0 0 0 1 0"
									/>
								</filter>
								<filter id={`bb-${ICON_DARK_FILTER_ID}`}>
									<feColorMatrix
										type="matrix"
										values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0"
									/>
								</filter>

								<radialGradient id="shine" cx="35%" cy="35%" r="65%">
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

								const filterId = bubble.iconUrl
									? getIconFilterId(bubble.item.vendor ?? '')
									: undefined;

								return (
									<g
										key={bubble.item.id}
										onMouseEnter={() => handleMouseEnter(bubble)}
										onMouseLeave={handleMouseLeave}
									>
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
												className="text-accent-red/30"
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
												fill="url(#shine)"
												opacity={0.15}
											/>

											{bubble.iconUrl ? (
												<image
													href={bubble.iconUrl}
													x={bubble.x - bubble.radius * 0.55}
													y={bubble.y - bubble.radius * 0.55}
													width={bubble.radius * 1.1}
													height={bubble.radius * 1.1}
													clipPath={`url(#clip-${bubble.item.id})`}
													filter={
														filterId
															? `url(#bb-${filterId})`
															: undefined
													}
													className="pointer-events-none"
												/>
											) : (
												<>
													<text
														x={bubble.x}
														y={bubble.y - (bubble.radius > 40 ? 6 : 0)}
														textAnchor="middle"
														dominantBaseline="central"
														fill={bubble.textColor}
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
															fontFamily:
																'var(--font-sans, system-ui)',
														}}
													>
														{getInitials(bubble.item.label)}
													</text>
													{bubble.radius > 40 && (
														<text
															x={bubble.x}
															y={
																bubble.y +
																(bubble.radius > 50 ? 12 : 8)
															}
															textAnchor="middle"
															dominantBaseline="central"
															fill={bubble.textColor}
															fontSize={bubble.radius > 50 ? 9 : 8}
															opacity={0.7}
															className="pointer-events-none select-none"
															style={{
																fontFamily:
																	'var(--font-sans, system-ui)',
															}}
														>
															{bubble.item.totalExecutions.toLocaleString()}
														</text>
													)}
												</>
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
					mode={mode}
					apis={apis}
					toolkits={toolkits}
					agents={agents}
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
	mode,
	apis: _apis,
	toolkits,
	agents: _agents,
	x,
	y,
	containerWidth,
}: {
	item: BubbleItem;
	mode: BubbleMode;
	apis: ApiUsageSummary[];
	toolkits: ToolkitUsageSummary[];
	agents: AgentUsageSummary[];
	x: number;
	y: number;
	containerWidth: number;
}): JSX.Element {
	const isRight = x > containerWidth / 2;

	if (mode === 'apis' && item.vendor) {
		const colors = getVendorConfig(item.vendor);
		const relatedToolkits = toolkits.filter((toolkit) =>
			toolkit.topApis.some((a) => a.vendor === item.vendor),
		);

		return (
			<div
				className="border-border bg-card pointer-events-none absolute z-20 w-56 rounded-lg border p-3 shadow-xl"
				style={{ left: isRight ? x - 240 : x + 20, top: Math.max(8, y - 60) }}
			>
				<div className="mb-2 flex items-center gap-2">
					<div
						className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-md"
						style={{ backgroundColor: colors.bg }}
					>
						{colors.iconUrl ? (
							<img
								src={colors.iconUrl}
								alt={item.label}
								className="h-5 w-5 object-contain"
								style={{ filter: colors.text === '#fff' ? 'invert(1)' : undefined }}
							/>
						) : (
							<span className="text-xs font-bold" style={{ color: colors.text }}>
								{getInitials(item.label)}
							</span>
						)}
					</div>
					<div>
						<p className="text-foreground text-sm font-medium">{item.label}</p>
						{item.subLabel && (
							<p className="text-muted-foreground text-xs">{item.subLabel}</p>
						)}
					</div>
				</div>

				<div className="mb-2 grid grid-cols-3 gap-2 text-center">
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

				{relatedToolkits.length > 0 && (
					<div className="border-border border-t pt-2">
						<p className="text-muted-foreground mb-1 text-[10px] font-medium tracking-wider uppercase">
							Used by
						</p>
						<div className="flex flex-wrap gap-1">
							{relatedToolkits.slice(0, 4).map((toolkit) => (
								<span
									key={toolkit.toolkitId}
									className="bg-muted text-foreground rounded-full px-1.5 py-0.5 text-[10px]"
								>
									{toolkit.toolkitName}
								</span>
							))}
							{relatedToolkits.length > 4 && (
								<span className="bg-muted text-muted-foreground rounded-full px-1.5 py-0.5 text-[10px]">
									+{relatedToolkits.length - 4}
								</span>
							)}
						</div>
					</div>
				)}
			</div>
		);
	}

	const topApis = item.topApis ?? [];

	return (
		<div
			className="border-border bg-card pointer-events-none absolute z-20 w-56 rounded-lg border p-3 shadow-xl"
			style={{ left: isRight ? x - 240 : x + 20, top: Math.max(8, y - 60) }}
		>
			<div className="mb-2">
				<p className="text-foreground text-sm font-medium">{item.label}</p>
				{item.subLabel && (
					<p className="text-muted-foreground text-xs capitalize">{item.subLabel}</p>
				)}
			</div>

			<div className="mb-2 grid grid-cols-3 gap-2 text-center">
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

			{topApis.length > 0 && (
				<div className="border-border border-t pt-2">
					<p className="text-muted-foreground mb-1 text-[10px] font-medium tracking-wider uppercase">
						Top APIs
					</p>
					<div className="space-y-1">
						{topApis.slice(0, 4).map((api) => {
							const cfg = getVendorConfig(api.vendor);
							return (
								<div
									key={`${api.vendor}:${api.apiName}`}
									className="flex items-center gap-1.5"
								>
									<div
										className="flex h-4 w-4 items-center justify-center rounded"
										style={{ backgroundColor: cfg.bg }}
									>
										{cfg.iconUrl ? (
											<img
												src={cfg.iconUrl}
												alt={api.apiName}
												className="h-3 w-3 object-contain"
												style={{
													filter:
														cfg.text === '#fff'
															? 'invert(1)'
															: undefined,
												}}
											/>
										) : (
											<span
												className="text-[7px] font-bold"
												style={{ color: cfg.text }}
											>
												{getInitials(api.apiName)}
											</span>
										)}
									</div>
									<span className="text-foreground flex-1 truncate text-[10px]">
										{api.apiName}
									</span>
									<span className="text-muted-foreground text-[10px]">
										{api.count}
									</span>
								</div>
							);
						})}
					</div>
				</div>
			)}
		</div>
	);
}
