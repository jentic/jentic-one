/**
 * Motion for the "Can call" list ⇄ cards lens switch (`ApiViewSwitch`).
 *
 * Every item carries its own index (`custom={{ i, n }}`) and computes its own
 * delay, so the choreography doesn't depend on nesting: a card, a row, its
 * elbow and its trunk segment all know where they sit in the sequence.
 *
 * - Cards: the grid fades up, each card staggered ≤24ms apart (the whole
 *   stagger capped at 150ms) with a fade-up + hint of scale.
 * - List ("branches joining the tree"): the trunk draws down from the card,
 *   segment by segment (scaleY 0→1 from the top); each row rises 14px into
 *   place, staggered top to bottom (≤35ms apart, capped so a long list lands
 *   in ~0.6s); and each row's elbow wipes out from the trunk as its row lands.
 *   The "+ Add APIs" branch is the last item, so it joins last.
 */
import type { Transition, Variants } from 'framer-motion';
import type { ApiView } from '@/modules/agents/lib/apiView';

/** An item's place in its layout: `i` of `n`. */
export interface LensItem {
	i: number;
	n: number;
}

/** The container's height tween between the layouts. */
export const LENS_HEIGHT_S = 0.24;

const CARD_STEP_S = 0.024;
const CARD_MAX_STAGGER_S = 0.15;
const CARD_S = 0.22;

const ROW_STEP_S = 0.035;
const ROW_MAX_STAGGER_S = 0.3;
const ROW_S = 0.28;
const ROW_RISE_PX = 14;
/** The shortest the whole trunk takes to draw, so a 2-row tree still reads as drawing. */
const TRUNK_MIN_S = 0.18;
/** The elbow starts wiping out this long into its row's rise, and lands with it. */
const ELBOW_LEAD_S = 0.12;

const EASE_OUT_SOFT: Transition['ease'] = [0.22, 1, 0.36, 1];

function step(n: number, perItem: number, cap: number): number {
	return n <= 1 ? 0 : Math.min(perItem, cap / (n - 1));
}

/** Card stagger step for `n` cards: ≤24ms, the last card starting ≤150ms in. */
export function lensStaggerStep(n: number): number {
	return step(n, CARD_STEP_S, CARD_MAX_STAGGER_S);
}

/** Row stagger step for `n` branches: ≤35ms, the last starting ≤300ms in. */
export function treeStaggerStep(n: number): number {
	return step(n, ROW_STEP_S, ROW_MAX_STAGGER_S);
}

/**
 * The whole layout. Cards fade up as a block (their own stagger rides on top);
 * the list holds still — its trunk, rows and elbows do the moving. Both leave
 * with a quick fade and a slight lift.
 */
export function lensLayoutVariants(view: ApiView): Variants {
	const exit = { opacity: 0, y: -4, transition: { duration: 0.12, ease: 'easeIn' } } as const;
	if (view === 'list') {
		return { hidden: { opacity: 1 }, visible: { opacity: 1, y: 0 }, exit };
	}
	return {
		hidden: { opacity: 0, y: 6, scale: 0.98 },
		visible: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.24, ease: 'easeOut' } },
		exit,
	};
}

/** Each card's (and the add tile's) entrance: a fade up with a hint of scale. */
export const LENS_CARD_VARIANTS: Variants = {
	hidden: { opacity: 0, y: 6, scale: 0.97 },
	visible: ({ i, n }: LensItem) => ({
		opacity: 1,
		y: 0,
		scale: 1,
		transition: { duration: CARD_S, ease: 'easeOut', delay: i * lensStaggerStep(n) },
	}),
};

/** A branch's row (or the Add APIs button): rises in from below. */
export const TREE_ROW_VARIANTS: Variants = {
	hidden: { opacity: 0, y: ROW_RISE_PX },
	visible: ({ i, n }: LensItem) => ({
		opacity: 1,
		y: 0,
		transition: { duration: ROW_S, ease: EASE_OUT_SOFT, delay: i * treeStaggerStep(n) },
	}),
};

/**
 * A branch's trunk segment: drawn downward from its top. The segments run
 * back to back (`i` starts as `i - 1` ends), so the trunk reads as one line
 * growing down from the card, a beat ahead of the rows it carries.
 */
export const TREE_TRUNK_VARIANTS: Variants = {
	hidden: { scaleY: 0 },
	visible: ({ i, n }: LensItem) => {
		const per = Math.max(treeStaggerStep(n), TRUNK_MIN_S / Math.max(1, n));
		return { scaleY: 1, transition: { duration: per, ease: 'linear', delay: i * per } };
	},
};

/** A branch's elbow: wipes out from the trunk toward its row as the row lands. */
export const TREE_ELBOW_VARIANTS: Variants = {
	hidden: { opacity: 0, clipPath: 'inset(0% 100% 0% 0%)' },
	visible: ({ i, n }: LensItem) => ({
		opacity: 1,
		clipPath: 'inset(0% 0% 0% 0%)',
		transition: {
			duration: ROW_S - ELBOW_LEAD_S,
			ease: 'easeOut',
			delay: i * treeStaggerStep(n) + ELBOW_LEAD_S,
		},
	}),
};
