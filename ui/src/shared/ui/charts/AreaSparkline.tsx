/**
 * AreaSparkline — a soft area sparkline that draws itself in: the line over a
 * fading fill in the current text colour. Promoted from the Monitor stat
 * strip so the agent rows can draw the same chart. It stretches to its box
 * (`preserveAspectRatio="none"`, non-scaling stroke), so size it with classes.
 *
 * Decorative (`aria-hidden`) — the number beside it is the data. All-zero data
 * draws a flat line along the bottom.
 */
import { useId } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

interface AreaSparklineProps {
	data: number[];
	className?: string;
}

/** The drawing's own coordinate box; the SVG stretches it to its CSS size. */
const W = 96;
const H = 28;

export function AreaSparkline({ data, className }: AreaSparklineProps) {
	const gradientId = useId();
	const clipId = useId();
	const reduce = useReducedMotion();
	if (data.length < 2) return null;
	const max = Math.max(...data, 1);
	const points = data.map((v, i) => [(i / (data.length - 1)) * W, H - 2 - (v / max) * (H - 4)]);
	const line = `M ${points.map(([x, y]) => `${x.toFixed(1)},${y!.toFixed(1)}`).join(' L ')}`;
	const area = `${line} L ${W},${H} L 0,${H} Z`;
	return (
		<svg
			viewBox={`0 0 ${W} ${H}`}
			className={className}
			preserveAspectRatio="none"
			aria-hidden="true"
		>
			<defs>
				<linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
					<stop offset="100%" stopColor="currentColor" stopOpacity="0" />
				</linearGradient>
				{/* The line draws in left to right through a widening clip — not
				    `pathLength`, whose dash maths breaks under the non-scaling
				    stroke once the chart is stretched. */}
				<clipPath id={clipId}>
					<motion.rect
						x={-2}
						y={-2}
						height={H + 4}
						initial={reduce ? false : { width: 0 }}
						animate={{ width: W + 4 }}
						transition={{ duration: 0.8, ease: [0.2, 0.8, 0.2, 1] }}
					/>
				</clipPath>
			</defs>
			<motion.path
				d={area}
				fill={`url(#${gradientId})`}
				initial={reduce ? false : { opacity: 0 }}
				animate={{ opacity: 1 }}
				transition={{ delay: 0.35, duration: 0.4 }}
			/>
			<path
				d={line}
				fill="none"
				stroke="currentColor"
				strokeWidth={1.5}
				strokeLinecap="round"
				strokeLinejoin="round"
				vectorEffect="non-scaling-stroke"
				clipPath={`url(#${clipId})`}
			/>
		</svg>
	);
}
