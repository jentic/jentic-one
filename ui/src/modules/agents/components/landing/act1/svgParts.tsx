/**
 * The SVG pieces both of Act 1's lanes draw: brand marks on light tiles, the
 * "your AI agent" cluster and its labels, the pulse ring, and the lanes' glide.
 */
import { motion, type Transition } from 'framer-motion';
import { AGENT_MARK_SLUGS, VENDOR_MARKS, type VendorMarkSlug } from '@/shared/ui';
import { EASE_GLIDE } from '@/modules/agents/components/landing/motion';

/**
 * A tile's inset and corner radius, as fractions of its size. A `ring` tile
 * (the agent cluster's) carries a hairline border and a little more inset.
 */
const TILE = {
	plain: { pad: 0.18, rx: 0.25 },
	ring: { pad: 0.2, rx: 0.26 },
} as const;

/** A brand mark on its light tile, drawn inside an SVG. */
export function SvgMark({
	slug,
	x,
	y,
	size,
	ring = false,
}: {
	slug: VendorMarkSlug;
	x: number;
	y: number;
	size: number;
	/** A hairline border around the tile. */
	ring?: boolean;
}) {
	const mark = VENDOR_MARKS[slug];
	const { pad: padRatio, rx } = TILE[ring ? 'ring' : 'plain'];
	const pad = size * padRatio;
	return (
		<g aria-hidden="true" data-vendor-mark={slug}>
			<rect
				x={x}
				y={y}
				width={size}
				height={size}
				rx={size * rx}
				fill="#fff"
				{...(ring && { strokeWidth: 1, className: 'stroke-border/60' })}
			/>
			<svg
				x={x + pad}
				y={y + pad}
				width={size - pad * 2}
				height={size - pad * 2}
				viewBox="0 0 24 24"
			>
				<path d={mark.path} fill={mark.hex} />
			</svg>
		</g>
	);
}

/**
 * "Your AI agent": the AI agent marks (Claude, OpenAI, Cursor, Gemini) as a
 * 2×2 grid of small ringed tiles centred on (cx, cy), no container. `tile` is
 * each tile's size; `dim` fades it (a paused agent).
 */
export function SvgAgentCluster({
	cx,
	cy,
	tile,
	dim,
}: {
	cx: number;
	cy: number;
	tile: number;
	dim?: boolean;
}) {
	const gap = Math.round(tile * 0.15);
	const size = tile * 2 + gap;
	const x0 = cx - size / 2;
	const y0 = cy - size / 2;
	return (
		<g
			aria-hidden="true"
			data-agent-mark=""
			opacity={dim ? 0.5 : 1}
			className="transition-opacity duration-500"
		>
			{AGENT_MARK_SLUGS.map((slug, k) => (
				<SvgMark
					key={slug}
					slug={slug}
					x={x0 + (k % 2) * (tile + gap)}
					y={y0 + Math.floor(k / 2) * (tile + gap)}
					size={tile}
					ring
				/>
			))}
		</g>
	);
}

/** "Your AI agent" over the cluster, the agent's name under it. */
export function AgentLabels({
	cx,
	top,
	bottom,
	name,
}: {
	cx: number;
	top: number;
	bottom: number;
	name: string;
}) {
	return (
		<g aria-hidden="true">
			<text
				x={cx}
				y={top}
				textAnchor="middle"
				fontSize={9.5}
				className="fill-muted-foreground"
			>
				Your AI agent
			</text>
			<text
				x={cx}
				y={bottom}
				textAnchor="middle"
				fontSize={9.5}
				className="fill-foreground font-mono"
			>
				{name}
			</text>
		</g>
	);
}

/** The accent ring shared by a Record card, its API box and its Activity row. */
export function PulseRing({
	x,
	y,
	w,
	h,
	rx,
}: {
	x: number;
	y: number;
	w: number;
	h: number;
	rx: number;
}) {
	return (
		<motion.rect
			x={x - 3}
			y={y - 3}
			width={w + 6}
			height={h + 6}
			rx={rx + 3}
			fill="none"
			strokeWidth={2}
			className="stroke-primary"
			initial={{ opacity: 0 }}
			animate={{ opacity: [0, 0.9, 0.2, 0.7, 0] }}
			transition={{ duration: 1.2 }}
		/>
	);
}

/** The lanes' glide between stops; instant under reduced motion. */
export function glide(reduced: boolean, ms = 1000): Transition {
	return reduced ? { duration: 0 } : { duration: ms / 1000, ease: EASE_GLIDE };
}
