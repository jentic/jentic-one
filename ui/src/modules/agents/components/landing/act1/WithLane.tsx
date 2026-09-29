/**
 * Act 1's WITH lane — the primary scene: the agent holds no keys and every
 * call walks the five stage cards (Find, Identity, Rules, Vault, Record)
 * before it reaches an API. Drawn in one SVG at a fixed intrinsic size (it
 * never scales up with the viewport, only down).
 *
 * Colours come from theme tokens via `fill-*` / `stroke-*` utilities, icons are
 * lucide, brand marks are the shared `VENDOR_MARKS` paths. Motion is transform /
 * opacity only and collapses to instant moves under reduced motion.
 */
import type { ComponentType, Ref, SVGProps } from 'react';
import { motion } from 'framer-motion';
import {
	AlertTriangle,
	CheckCircle2,
	Fingerprint,
	KeyRound,
	LineChart,
	Pause,
	Scale,
	Search,
	ShieldCheck,
	XCircle,
} from 'lucide-react';
import { JenticLogo } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	CHECKPOINTS,
	callTag,
	type CardTone,
	type Checkpoint,
	type WhyState,
} from '@/modules/agents/components/landing/act1/model';
import {
	DEMO_AGENT,
	DEMO_APIS,
	STORY_APIS,
	type DemoApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';
import { apiY, cardCx, type Geo } from '@/modules/agents/components/landing/act1/laneGeometry';
import { KeyPill } from '@/modules/agents/components/landing/act1/KeyPill';
import {
	AgentLabels,
	PulseRing,
	SvgAgentCluster,
	SvgMark,
	glide,
} from '@/modules/agents/components/landing/act1/svgParts';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const STAGES: Record<Checkpoint, { title: string; does: string; icon: Icon }> = {
	find: { title: 'Find', does: 'finds the API call', icon: Search },
	identity: { title: 'Identity', does: 'who is asking', icon: Fingerprint },
	rules: { title: 'Rules', does: 'allow or deny', icon: Scale },
	vault: { title: 'Vault', does: 'adds the key', icon: ShieldCheck },
	record: { title: 'Record', does: 'traces the call', icon: LineChart },
};

/** A verdict is 11px heading type: about 6.2px a character. */
const VERDICT_CHAR_W = 6.2;
/** Mono glyphs are about 0.6em wide. */
const MONO_EM = 0.6;

const CARD_STROKE: Record<CardTone, string> = {
	idle: 'stroke-border',
	active: 'stroke-primary',
	ok: 'stroke-success',
	warn: 'stroke-warning',
	fail: 'stroke-danger',
};
const CARD_FILL: Record<CardTone, string> = {
	idle: 'fill-card',
	active: 'fill-primary/10',
	ok: 'fill-success/[0.07]',
	warn: 'fill-warning/10',
	fail: 'fill-danger/10',
};
const VERDICT_FILL: Record<CardTone, string> = {
	idle: 'fill-muted-foreground',
	active: 'fill-primary',
	ok: 'fill-success',
	warn: 'fill-warning',
	fail: 'fill-danger',
};

function VerdictIcon({
	tone,
	vault,
	x,
	y,
}: {
	tone: CardTone;
	vault: boolean;
	x: number;
	y: number;
}) {
	const p = { x, y, width: 12, height: 12, 'aria-hidden': true } as const;
	if (tone === 'fail') return <XCircle {...p} className="text-danger" />;
	if (tone === 'warn') return <AlertTriangle {...p} className="text-warning" />;
	if (vault) return <KeyRound {...p} className="text-success" />;
	return <CheckCircle2 {...p} className="text-success" />;
}

function StageCard({
	id,
	i,
	state,
	reduced,
	g,
}: {
	id: Checkpoint;
	i: number;
	g: Geo;
	state: WhyState;
	reduced: boolean;
}) {
	const stage = STAGES[id];
	const c = state.cards[id];
	const onIt = state.dotAt === i && c.tone !== 'idle';
	const tone: CardTone = onIt && c.tone === 'ok' ? 'active' : c.tone;
	const x = g.cardX(i);
	const cx = cardCx(g, i);
	const IconCmp = stage.icon;
	const verdictW = c.verdict.length * VERDICT_CHAR_W + 16;
	const pulsing = id === 'record' && state.pulse !== null && state.dotAt === 4;
	return (
		<g data-stage={id} data-tone={c.tone} data-pulse={pulsing || undefined}>
			<rect
				x={x}
				y={g.cardY}
				width={g.cardW}
				height={g.cardH}
				rx={12}
				strokeWidth={tone === 'active' || tone === 'fail' || tone === 'warn' ? 2 : 1.25}
				className={cn('transition-colors duration-300', CARD_STROKE[tone], CARD_FILL[tone])}
			/>
			{pulsing && !reduced && <PulseRing x={x} y={g.cardY} w={g.cardW} h={g.cardH} rx={12} />}
			<IconCmp
				x={cx - 9}
				y={g.cardY + 12}
				width={18}
				height={18}
				className={tone === 'idle' ? 'text-muted-foreground' : 'text-foreground'}
				aria-hidden="true"
			/>
			<text
				x={cx}
				y={g.cardY + 48}
				textAnchor="middle"
				fontSize={13}
				className="fill-foreground font-heading font-semibold"
			>
				{stage.title}
			</text>
			<text
				x={cx}
				y={g.cardY + 62}
				textAnchor="middle"
				fontSize={10}
				className="fill-muted-foreground"
			>
				{stage.does}
			</text>
			<line
				x1={x + 10}
				y1={g.cardY + 84}
				x2={x + g.cardW - 10}
				y2={g.cardY + 84}
				className="stroke-border"
			/>
			{c.lines.map((line, n) => (
				<text
					key={n}
					x={cx}
					y={g.cardY + 101 + n * 14}
					textAnchor="middle"
					// Long lines shrink to fit the card rather than spill past it.
					fontSize={Math.min(9.5, (g.cardW - 10) / (line.length * MONO_EM))}
					className="fill-foreground/85 font-mono"
				>
					{line}
				</text>
			))}
			{c.verdict && (
				<g>
					<VerdictIcon
						tone={c.tone}
						vault={id === 'vault'}
						x={cx - verdictW / 2}
						y={g.cardY + 134}
					/>
					<text
						x={cx - verdictW / 2 + 16}
						y={g.cardY + 144}
						fontSize={11}
						className={cn('font-heading font-semibold', VERDICT_FILL[c.tone])}
					>
						{c.verdict}
					</text>
				</g>
			)}
		</g>
	);
}

/** Where the call chip sits for `dotAt`. */
function dotPoint(state: WhyState, g: Geo): { x: number; y: number } {
	if (state.dotAt < 0) return { x: g.agentX, y: g.pipeY };
	if (state.dotAt < CHECKPOINTS.length) return { x: cardCx(g, state.dotAt), y: g.pipeY };
	const api = state.call?.api ?? 'github';
	return { x: g.apiX - 8, y: apiY(Math.max(0, STORY_APIS.indexOf(api))) };
}

const DOT_FILL = { default: 'fill-primary', warn: 'fill-warning', fail: 'fill-danger' } as const;

export function WithLane({
	state,
	reduced,
	g,
	laneRef,
	hoverApi,
}: {
	state: WhyState;
	reduced: boolean;
	/** The lanes' shared geometry. */
	g: Geo;
	/** Measures the lanes' width. */
	laneRef?: Ref<HTMLElement>;
	/** An Activity row under the pointer: its API box lights up. */
	hoverApi?: DemoApiId | null;
}) {
	const dot = dotPoint(state, g);
	const showDot = state.call !== null;
	const target = state.call?.api;
	const paused = state.agentStatus === 'disabled';
	return (
		<section
			ref={laneRef}
			aria-label="With Jentic One"
			className="border-success/40 bg-success/[0.04] rounded-xl border px-4 pt-3 pb-3"
			data-testid="with-lane"
		>
			<div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
				<h3 className="font-heading text-success text-sm font-semibold">With Jentic One</h3>
				<p className="text-success/85 font-mono text-[11px]">{state.withNote}</p>
			</div>
			<svg
				viewBox={`0 0 ${g.w} ${g.h}`}
				width={g.w}
				height={g.h}
				className="mx-auto mt-2 block h-auto w-full"
				style={{ maxWidth: g.w }}
				role="img"
				aria-label="Every call passes Find, Identity, Rules, Vault and Record before it reaches an API. The agent holds no keys."
			>
				<line
					x1={g.agentX + 34}
					y1={g.pipeY}
					x2={g.boxL}
					y2={g.pipeY}
					className="stroke-border"
					strokeWidth={1.5}
					strokeDasharray="4 6"
				/>
				{STORY_APIS.map((api, i) => (
					<path
						key={api}
						d={`M${g.boxL + g.boxW} ${g.pipeY} C ${g.apiX - 40} ${g.pipeY}, ${g.apiX - 40} ${apiY(i)}, ${g.apiX - 4} ${apiY(i)}`}
						fill="none"
						className={target === api ? 'stroke-primary/70' : 'stroke-border'}
						strokeWidth={1.25}
						strokeDasharray="4 6"
					/>
				))}

				<g data-testid="with-agent">
					<AgentLabels
						cx={g.agentX}
						top={g.pipeY - 40}
						bottom={g.pipeY + 45}
						name={DEMO_AGENT.name}
					/>
					<SvgAgentCluster cx={g.agentX} cy={g.pipeY} tile={28} dim={paused} />
					{paused ? (
						<g>
							<rect
								x={g.agentX - 30}
								y={g.pipeY + 52}
								width={60}
								height={16}
								rx={8}
								className="fill-warning/15 stroke-warning/60"
							/>
							<Pause
								x={g.agentX - 24}
								y={g.pipeY + 55}
								width={10}
								height={10}
								className="text-foreground"
								aria-hidden="true"
							/>
							<text
								x={g.agentX - 11}
								y={g.pipeY + 64}
								fontSize={10}
								className="fill-foreground font-mono"
							>
								Paused
							</text>
						</g>
					) : (
						<KeyPill cx={g.agentX} y={g.pipeY + 51} tone="safe" />
					)}
				</g>

				<rect
					x={g.boxL}
					y={4}
					width={g.boxW}
					height={g.h - 8}
					rx={14}
					className="fill-background/60 stroke-border"
					strokeWidth={1.25}
				/>
				{/* The gateway's header: the Jentic mark, then what it does. */}
				<foreignObject
					x={g.boxL + g.boxW / 2 - 150}
					y={11}
					width={20}
					height={14}
					data-testid="gateway-logo"
				>
					<JenticLogo showWord={false} showBadge={false} width={20} height={14} />
				</foreignObject>
				<text
					x={g.boxL + g.boxW / 2 + 12}
					y={21}
					textAnchor="middle"
					fontSize={9.5}
					letterSpacing={1.6}
					className="fill-muted-foreground font-mono"
				>
					JENTIC ONE · EVERY CALL PASSES THROUGH
				</text>
				{CHECKPOINTS.map((id, i) => (
					<StageCard key={id} id={id} i={i} state={state} reduced={reduced} g={g} />
				))}

				{STORY_APIS.map((api, i) => {
					const y = apiY(i);
					const hit = state.apiHit[api];
					const connected = state.connected[api];
					const lit = hoverApi === api;
					const pulsing = state.pulse !== null && target === api && state.dotAt === 4;
					return (
						<g key={api} data-with-api={api}>
							<rect
								x={g.apiX}
								y={y - g.apiH / 2}
								width={g.apiW}
								height={g.apiH}
								rx={9}
								strokeWidth={hit || lit ? 2 : 1.25}
								className={cn(
									'fill-card transition-colors duration-300',
									lit
										? 'stroke-primary'
										: hit === 'ok'
											? 'stroke-success'
											: hit === 'fail'
												? 'stroke-danger'
												: 'stroke-border',
								)}
							/>
							{pulsing && !reduced && (
								<PulseRing
									x={g.apiX}
									y={y - g.apiH / 2}
									w={g.apiW}
									h={g.apiH}
									rx={9}
								/>
							)}
							<SvgMark slug={api} x={g.apiX + 9} y={y - 10} size={20} />
							<text
								x={g.apiX + 37}
								y={y - 2}
								fontSize={12}
								className="fill-foreground font-heading"
							>
								{DEMO_APIS[api].name}
							</text>
							<text
								x={g.apiX + 37}
								y={y + 11}
								fontSize={9.5}
								className={cn(
									'font-mono',
									connected ? 'fill-success' : 'fill-muted-foreground',
								)}
							>
								{connected ? 'connected' : 'not connected'}
							</text>
						</g>
					);
				})}

				<motion.g
					data-testid="call-dot"
					initial={false}
					animate={{ x: dot.x, y: dot.y, opacity: showDot ? 1 : 0 }}
					transition={glide(reduced)}
				>
					<circle r={9} className={cn('transition-colors', DOT_FILL[state.dotTone])} />
					{state.call && (
						<text
							y={3.5}
							textAnchor="middle"
							fontSize={10}
							className="fill-background font-mono font-bold"
						>
							{callTag(state.call)}
						</text>
					)}
					{state.keyed && (
						<KeyRound
							x={10}
							y={-16}
							width={12}
							height={12}
							className="text-warning"
							aria-hidden="true"
						/>
					)}
					{state.dotLabel && (state.dotAt < 0 || state.dotAt > 4) && (
						<g>
							<rect
								x={-36}
								y={-30}
								width={72}
								height={16}
								rx={8}
								className="fill-background stroke-border"
							/>
							<text
								y={-19}
								textAnchor="middle"
								fontSize={9.5}
								className={cn(
									'font-mono',
									state.dotTone === 'fail' ? 'fill-danger' : 'fill-warning',
								)}
							>
								{state.dotLabel}
							</text>
						</g>
					)}
				</motion.g>
			</svg>
		</section>
	);
}
