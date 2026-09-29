/**
 * Act 1's WITHOUT lane — the quieter comparison under the WITH lane: the agent
 * holds every story API's key (its .env) and calls each API directly, in the
 * same order as the WITH lane, each call getting whatever a key-holding script
 * gets. A red dot flies to the API the WITH lane is working on, straight
 * through the missing gateway, and the consequence lands beside the box.
 * Quieter than the WITH lane, but every loss is spelled out.
 */
import type { ComponentType, SVGProps } from 'react';
import { motion } from 'framer-motion';
import { AlertTriangle, Ban, EyeOff, KeyRound, ShieldAlert, Trash2 } from 'lucide-react';
import { JenticLogo } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { WhyState } from '@/modules/agents/components/landing/act1/model';
import {
	DEMO_AGENT,
	DEMO_APIS,
	ENV_SECRETS,
	STORY_APIS,
	type StoryApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';
import { NB, without, type Geo } from '@/modules/agents/components/landing/act1/laneGeometry';
import { KeyPill, KeyPillTooltip } from '@/modules/agents/components/landing/act1/KeyPill';
import {
	AgentLabels,
	SvgAgentCluster,
	SvgMark,
} from '@/modules/agents/components/landing/act1/svgParts';
import { EASE_GLIDE, cubicPath, cubicSamples } from '@/modules/agents/components/landing/motion';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const WITHOUT_SUBTITLE = 'any script holding a key just works — nobody knows which agent it was';

const OUTCOME_ICON: Record<StoryApiId, Icon> = {
	github: EyeOff,
	gmail: KeyRound,
	slack: AlertTriangle,
	googledrive: Trash2,
};

/** An outcome is 10px mono: about 6.1px a character, plus icon and padding. */
const OUTCOME_CHAR_W = 6.1;

/** Points along trunk + wire to API `i`, as keyframes for the travelling call dot. */
function wireKeyframes(n: ReturnType<typeof without>, i: number): { x: number[]; y: number[] } {
	const wire = n.wire(i);
	const along = cubicSamples(wire, 14);
	// Straight through the missing gateway first: nothing stops it there.
	return {
		x: [n.trunkStart.x, (n.trunkStart.x + wire[0].x) / 2, ...along.x],
		y: [n.trunkStart.y, n.trunkStart.y, ...along.y],
	};
}

export function WithoutLane({ state, reduced, g }: { state: WhyState; reduced: boolean; g: Geo }) {
	const n = without(g);
	const firing = state.withoutFiring;
	const firingIdx = firing ? STORY_APIS.indexOf(firing) : -1;
	return (
		<section
			aria-label="Without Jentic One"
			className="border-danger/30 bg-danger/[0.03] relative rounded-xl border px-4 pt-2.5 pb-2"
			data-testid="without-lane"
		>
			<div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
				<h3 className="font-heading text-danger/90 text-sm font-semibold">
					Without Jentic One
				</h3>
				<p className="text-muted-foreground font-mono text-[11px]">{WITHOUT_SUBTITLE}</p>
			</div>
			{/* Sized like the SVG, so the pill's tooltip trigger can sit over the pill. */}
			<div className="relative mx-auto mt-1" style={{ maxWidth: g.w }}>
				<svg
					viewBox={`0 0 ${g.w} ${NB.h}`}
					width={g.w}
					height={NB.h}
					className="block h-auto w-full"
					role="img"
					aria-label={`Without a gateway the agent holds ${ENV_SECRETS.length} keys and calls every API directly: nothing is asked, checked or recorded.`}
				>
					{STORY_APIS.map((api, i) => (
						<path
							key={api}
							d={cubicPath(n.wire(i))}
							fill="none"
							strokeWidth={1.25}
							strokeDasharray="4 6"
							className={cn(
								'transition-colors duration-300',
								firing === api ? 'stroke-danger/70' : 'stroke-border',
							)}
						/>
					))}
					<line
						x1={n.trunkStart.x}
						y1={NB.agentY}
						x2={n.gapX - NB.gapR - 4}
						y2={NB.agentY}
						strokeWidth={1.25}
						strokeDasharray="4 6"
						className={firing ? 'stroke-danger/70' : 'stroke-border'}
					/>
					<line
						x1={n.gapX + NB.gapR + 4}
						y1={NB.agentY}
						x2={n.wireStart.x}
						y2={NB.agentY}
						className="stroke-border"
					/>

					{/* The layer that isn't there: a dimmed Jentic mark, crossed out. */}
					<g data-testid="no-gateway" aria-hidden="true">
						<circle
							cx={n.gapX}
							cy={NB.agentY}
							r={NB.gapR}
							strokeWidth={1.25}
							strokeDasharray="3 3"
							className="fill-card stroke-border"
						/>
						<foreignObject
							x={n.gapX - 9}
							y={NB.agentY - 6.5}
							width={18}
							height={13}
							opacity={0.35}
							style={{ filter: 'grayscale(1)' }}
						>
							<JenticLogo showWord={false} showBadge={false} width={18} height={13} />
						</foreignObject>
						<circle cx={n.gapX + 11} cy={NB.agentY - 11} r={7} className="fill-card" />
						<Ban
							x={n.gapX + 5}
							y={NB.agentY - 17}
							width={12}
							height={12}
							className="text-danger/80"
						/>
						<text
							x={n.gapX}
							y={NB.agentY + NB.gapR + 14}
							textAnchor="middle"
							fontSize={9.5}
							className="fill-muted-foreground font-mono"
						>
							No gateway
						</text>
					</g>

					<g data-testid="without-agent">
						<AgentLabels
							cx={g.agentX}
							top={NB.agentY - NB.tile - 8}
							bottom={NB.agentY + NB.tile + 11}
							name={DEMO_AGENT.name}
						/>
						<SvgAgentCluster cx={g.agentX} cy={NB.agentY} tile={NB.tile} />
						<KeyPill cx={g.agentX} y={NB.pillY} tone="exposed" />
					</g>

					{STORY_APIS.map((api, i) => {
						const y = n.apiY(i);
						const out = state.without[api];
						const destructive = api === 'googledrive' && state.withoutLost > 0;
						const OutIcon = OUTCOME_ICON[api];
						const outW = out ? out.length * OUTCOME_CHAR_W + 34 : 0;
						const chipX = g.apiX - outW - 12;
						return (
							<g key={api} data-without-api={api}>
								{out && (
									<motion.g
										initial={reduced ? false : { opacity: 0, x: 8 }}
										animate={{ opacity: 1, x: 0 }}
										transition={{
											duration: reduced ? 0 : 0.5,
											ease: EASE_GLIDE,
										}}
									>
										<rect
											x={chipX}
											y={y - 10}
											width={outW}
											height={20}
											rx={10}
											strokeWidth={destructive ? 1.5 : 1}
											className={
												destructive
													? 'fill-danger/20 stroke-danger'
													: 'fill-warning/10 stroke-warning/50'
											}
										/>
										<OutIcon
											x={chipX + 8}
											y={y - 5.5}
											width={11}
											height={11}
											className={destructive ? 'text-danger' : 'text-warning'}
											aria-hidden="true"
										/>
										<text
											x={chipX + 24}
											y={y + 3.5}
											fontSize={10}
											className={cn(
												'font-mono',
												destructive
													? 'fill-danger font-semibold'
													: 'fill-warning',
											)}
										>
											{out}
										</text>
									</motion.g>
								)}
								<rect
									x={g.apiX}
									y={y - NB.apiH / 2}
									width={g.apiW}
									height={NB.apiH}
									rx={7}
									strokeWidth={destructive ? 1.75 : 1.25}
									className={cn(
										'fill-card transition-colors duration-300',
										destructive
											? 'stroke-danger'
											: out
												? 'stroke-warning/40'
												: 'stroke-border',
									)}
								/>
								<SvgMark slug={api} x={g.apiX + 7} y={y - 8} size={16} />
								<text
									x={g.apiX + 30}
									y={y + 4}
									fontSize={11}
									className="fill-muted-foreground font-heading"
								>
									{DEMO_APIS[api].name}
								</text>
							</g>
						);
					})}

					{firingIdx >= 0 && !reduced && (
						<motion.g
							key={firing}
							data-testid="without-dot"
							initial={{ opacity: 0, x: n.trunkStart.x, y: n.trunkStart.y }}
							animate={{ opacity: [0, 1, 1, 1], ...wireKeyframes(n, firingIdx) }}
							transition={{ duration: 1.2, ease: EASE_GLIDE }}
						>
							<circle r={5} className="fill-danger" />
						</motion.g>
					)}
				</svg>
				{/* The pill's keys, for anyone curious. */}
				<KeyPillTooltip cx={g.agentX} y={NB.pillY} w={g.w} h={NB.h} />
			</div>
			<WhatWentWrong state={state} reduced={reduced} />
		</section>
	);
}

/** The WITHOUT lane's running tally of losses, filled in as the story reaches each. */
function WhatWentWrong({ state, reduced }: { state: WhyState; reduced: boolean }) {
	const items: { key: string; text: string; on: boolean; strong?: boolean }[] = [
		{ key: 'keys', text: `${ENV_SECRETS.length} keys exposed to the agent`, on: !state.intro },
		{ key: 'approvals', text: '0 approvals', on: state.stats.approvals > 0 },
		{ key: 'recorded', text: '0 calls recorded', on: state.stats.traced > 0 },
		{
			key: 'files',
			text: `${state.withoutLost} files lost`,
			on: state.withoutLost > 0,
			strong: true,
		},
		{
			key: 'who',
			text: 'no way to tell which agent did it',
			on: state.agentStatus === 'disabled' || state.settled,
		},
	];
	const shown = items.filter((i) => i.on);
	return (
		<p
			data-testid="what-went-wrong"
			className="border-danger/20 mt-1.5 flex min-h-6 flex-wrap items-center gap-x-1.5 gap-y-0.5 border-t pt-1.5 text-[11px]"
		>
			<ShieldAlert className="text-danger/80 h-3.5 w-3.5" aria-hidden="true" />
			<span className="text-danger/90 font-medium">What went wrong:</span>
			{shown.length === 0 && <span className="text-muted-foreground">nothing yet</span>}
			{shown.map((item, k) => (
				<motion.span
					key={item.key}
					initial={reduced ? false : { opacity: 0, y: 3 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ duration: reduced ? 0 : 0.4, ease: EASE_GLIDE }}
					className={cn(
						'font-mono',
						item.strong ? 'text-danger font-semibold' : 'text-muted-foreground',
					)}
				>
					{k > 0 && (
						<span className="text-border mr-1.5" aria-hidden="true">
							·
						</span>
					)}
					{item.text}
				</motion.span>
			))}
		</p>
	);
}
