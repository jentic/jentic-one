/**
 * Act 1's lane geometry, at 1:1 with CSS pixels. `BASE` is the tightest
 * layout: each lane's SVG only ever scales down from it, never up. When the
 * lanes have spare width, `geom` spends it on the connecting lines, never on
 * the elements: half lengthens the agent's lead-in to the gateway, half the
 * fan-out to the API column. Both lanes share one geometry, measured once.
 */
import { useLayoutEffect, useState } from 'react';

const BASE = {
	w: 1044,
	h: 212,
	// Room left of the agent for its key pill, centred under it.
	agentX: 68,
	pipeY: 116,
	boxL: 134,
	boxW: 716,
	// Cards wide enough for every scripted detail line on one line.
	cardX: (i: number) => 146 + i * 140,
	cardW: 132,
	cardY: 32,
	cardH: 160,
	apiX: 880,
	apiW: 160,
	apiH: 38,
};
/** The most spare width the lines absorb; past it the diagram just centres. */
const MAX_SPREAD = 280;

export type Geo = typeof BASE;

function geom(spread: number): Geo {
	const extra = Math.max(0, Math.min(MAX_SPREAD, Math.round(spread)));
	const lead = Math.round(extra / 2);
	return {
		...BASE,
		w: BASE.w + extra,
		boxL: BASE.boxL + lead,
		cardX: (i: number) => BASE.cardX(i) + lead,
		apiX: BASE.apiX + extra,
	};
}

/**
 * The lanes' geometry, from one lane's content width (BASE until measured, and
 * in tests). Attach the returned ref to a lane `<section>`; every lane has the
 * same width and padding.
 */
export function useLaneGeometry(): [Geo, (el: HTMLElement | null) => void] {
	const [el, setEl] = useState<HTMLElement | null>(null);
	const [spread, setSpread] = useState(0);
	useLayoutEffect(() => {
		if (!el) return undefined;
		const update = () => {
			const cs = getComputedStyle(el);
			const inner =
				el.clientWidth -
				parseFloat(cs.paddingLeft || '0') -
				parseFloat(cs.paddingRight || '0');
			setSpread(Math.max(0, inner - BASE.w));
		};
		update();
		if (typeof ResizeObserver === 'undefined') return undefined;
		const ro = new ResizeObserver(update);
		ro.observe(el);
		return () => ro.disconnect();
	}, [el]);
	return [geom(spread), setEl];
}

/* --------------------------------------------------------------------- WITH */

export const cardCx = (g: Geo, i: number) => g.cardX(i) + g.cardW / 2;

/** The WITH lane's API box centres, fanned around the pipe. */
export const apiY = (i: number) => BASE.pipeY + (i - 1.5) * 50;

/* ------------------------------------------------------------------ WITHOUT */

/** The WITHOUT lane's fixed geometry; x positions come from the shared `Geo`. */
export const NB = {
	h: 112,
	agentY: 38,
	tile: 22,
	gapR: 15,
	apiH: 24,
	apiGap: 26,
	// The key pill sits under the agent's name.
	pillY: 76,
};

/** Where the WITHOUT lane's pieces sit for a given WITH-lane geometry. */
export function without(g: Geo) {
	const trunkStart = { x: g.agentX + NB.tile + 8, y: NB.agentY };
	// Where the gateway would be: the middle of the WITH lane's gateway box.
	const gapX = g.boxL + g.boxW / 2 - 60;
	const wireStart = { x: gapX + NB.gapR + 4, y: NB.agentY };
	const apiY = (i: number) => NB.agentY + 18 + (i - 1.5) * NB.apiGap;
	/** The wire from the missing gateway to API `i`. */
	const wire = (i: number) => {
		const end = { x: g.apiX - 4, y: apiY(i) };
		const midX = wireStart.x + (end.x - wireStart.x) * 0.55;
		return [wireStart, { x: midX, y: wireStart.y }, { x: midX, y: end.y }, end] as const;
	};
	return { trunkStart, gapX, wireStart, apiY, wire };
}
