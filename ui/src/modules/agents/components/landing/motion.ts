/**
 * The tour's shared motion vocabulary: its easing curves, and the cubic Bézier
 * a travelling badge or dot follows.
 */

/** Soft ease-out, the tour's default (the CSS `--ease-out-soft` curve). */
export const EASE_OUT_SOFT = [0.22, 1, 0.36, 1] as const;

/** Soft in-out: a chip glides between stops rather than snapping. */
export const EASE_GLIDE = [0.45, 0, 0.25, 1] as const;

export interface Pt {
	x: number;
	y: number;
}

/** A cubic Bézier: start, two control points, end. */
export type Cubic = readonly [Pt, Pt, Pt, Pt];

/** The curve as an SVG path `d`. */
export function cubicPath([a, b, c, d]: Cubic): string {
	return `M${a.x} ${a.y} C ${b.x} ${b.y}, ${c.x} ${c.y}, ${d.x} ${d.y}`;
}

/** `steps + 1` evenly spaced (in t) points along the curve, as x / y keyframes. */
export function cubicSamples([a, b, c, d]: Cubic, steps: number): { x: number[]; y: number[] } {
	const x: number[] = [];
	const y: number[] = [];
	for (let k = 0; k <= steps; k++) {
		const t = k / steps;
		const u = 1 - t;
		const w = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
		x.push(w[0] * a.x + w[1] * b.x + w[2] * c.x + w[3] * d.x);
		y.push(w[0] * a.y + w[1] * b.y + w[2] * c.y + w[3] * d.y);
	}
	return { x, y };
}
