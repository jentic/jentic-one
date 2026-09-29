/**
 * The landing's scene controller — one clock, one reducer, pure scenes.
 *
 * A script is an initial state plus a list of beats; each beat folds the state
 * forward and names the caption shown while it holds. Scenes render only from
 * the folded state, so seeking is "fold the first N beats": play, pause, seek,
 * replay and reduced motion all show exactly the same frames, and every frame
 * is a valid still.
 */

export interface Beat<S> {
	/** Stable id (seek target, React key). */
	id: string;
	/** How long this beat holds before the next one, while playing. */
	durationMs: number;
	/** The one sentence announced while this beat is on screen. */
	caption: string;
	/** Fold this beat into the state. Pure. */
	apply: (state: S) => S;
}

export interface Script<S> {
	initial: S;
	beats: readonly Beat<S>[];
}

/**
 * Every beat's resulting state, in order: `frames[i]` is the state after beats
 * `0..i`. Computed once per script, so seeking is an index lookup.
 */
export function foldFrames<S>(script: Script<S>): S[] {
	const frames: S[] = [];
	let state = script.initial;
	for (const beat of script.beats) {
		state = beat.apply(state);
		frames.push(state);
	}
	return frames;
}

/** The index of the beat with `id`, or -1. */
export function beatIndexOf<S>(script: Script<S>, id: string): number {
	return script.beats.findIndex((b) => b.id === id);
}

/** Clamp an index into the script's beat range. */
export function clampBeat<S>(script: Script<S>, index: number): number {
	return Math.max(0, Math.min(script.beats.length - 1, index));
}
