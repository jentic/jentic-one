/**
 * useLandingTimeline — drives a `Script` with one animation-frame clock.
 *
 * It plays only while every condition holds: the viewer hasn't paused it, the
 * tab is visible, at least a quarter of the stage is on screen, focus is not
 * inside the stage (unless `pauseOnFocus` is off), and motion is allowed. A
 * control that lives in the stage (marked `data-stage-control`) doesn't count
 * as focus inside it: using it is watching, not reading. Under reduced motion
 * nothing autoplays: the same frames are stepped with Prev / Next, as
 * annotated stills.
 *
 * `progress` (0..1 through the current beat) and `overall` (0..1 through the
 * script) are MotionValues, so a progress bar follows the clock without
 * re-rendering the scene every frame.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import {
	useAnimationFrame,
	useMotionValue,
	useReducedMotionConfig,
	type MotionValue,
} from 'framer-motion';
import {
	beatIndexOf,
	clampBeat,
	foldFrames,
	type Beat,
	type Script,
} from '@/modules/agents/components/landing/timeline';

export interface LandingTimelineOptions {
	/** Start playing on mount (ignored under reduced motion). Default true. */
	autoplay?: boolean;
	/** Wrap after the last beat instead of stopping. Default false. */
	loop?: boolean;
	/** The beat a loop wraps to (e.g. past a one-time intro). Default 0. */
	loopFrom?: number;
	/** Hold the clock while focus is inside the stage. Default true. */
	pauseOnFocus?: boolean;
	/** Override the reduced-motion preference (tests; otherwise MotionConfig / OS). */
	reducedMotion?: boolean;
}

export interface LandingTimeline<S> {
	state: S;
	index: number;
	beat: Beat<S>;
	count: number;
	/** The clock is actually advancing right now. */
	running: boolean;
	/** The viewer's own play/pause choice. */
	playing: boolean;
	/** Reduced motion: stills only, no autoplay. */
	reduced: boolean;
	atEnd: boolean;
	play: () => void;
	pause: () => void;
	toggle: () => void;
	seek: (target: number | string) => void;
	next: () => void;
	prev: () => void;
	restart: () => void;
	progress: MotionValue<number>;
	overall: MotionValue<number>;
	/** Attach to the stage: its visibility and focus hold the clock. */
	stageRef: RefObject<HTMLDivElement | null>;
}

function usePageVisible(): boolean {
	const [visible, setVisible] = useState(
		() => typeof document === 'undefined' || document.visibilityState !== 'hidden',
	);
	useEffect(() => {
		const onChange = () => setVisible(document.visibilityState !== 'hidden');
		document.addEventListener('visibilitychange', onChange);
		return () => document.removeEventListener('visibilitychange', onChange);
	}, []);
	return visible;
}

/** At least a quarter of the element is on screen (true until measured). */
function useMostlyInView(ref: RefObject<HTMLElement | null>): boolean {
	const [inView, setInView] = useState(true);
	useEffect(() => {
		const el = ref.current;
		if (!el || typeof IntersectionObserver === 'undefined') return undefined;
		const io = new IntersectionObserver(
			([entry]) => setInView(entry.intersectionRatio >= 0.25),
			{ threshold: [0, 0.25, 0.5, 1] },
		);
		io.observe(el);
		return () => io.disconnect();
	}, [ref]);
	return inView;
}

/** Marks a control inside the stage whose focus does not hold the clock. */
const STAGE_CONTROL = '[data-stage-control]';

/** Focus is inside the element, on anything but a `data-stage-control`. */
function useFocusWithin(ref: RefObject<HTMLElement | null>): boolean {
	const [inside, setInside] = useState(false);
	useEffect(() => {
		const el = ref.current;
		if (!el) return undefined;
		const holds = (target: EventTarget | null) =>
			target instanceof Element && el.contains(target) && !target.closest(STAGE_CONTROL);
		const onIn = (e: FocusEvent) => setInside(holds(e.target));
		const onOut = (e: FocusEvent) => setInside(holds(e.relatedTarget));
		el.addEventListener('focusin', onIn);
		el.addEventListener('focusout', onOut);
		return () => {
			el.removeEventListener('focusin', onIn);
			el.removeEventListener('focusout', onOut);
		};
	}, [ref]);
	return inside;
}

export function useLandingTimeline<S>(
	script: Script<S>,
	{
		autoplay = true,
		loop = false,
		loopFrom = 0,
		pauseOnFocus = true,
		reducedMotion,
	}: LandingTimelineOptions = {},
): LandingTimeline<S> {
	const configReduced = useReducedMotionConfig();
	const reduced = reducedMotion ?? configReduced === true;
	const frames = useMemo(() => foldFrames(script), [script]);
	const count = script.beats.length;

	const [index, setIndex] = useState(0);
	const [playing, setPlaying] = useState(autoplay && !reduced);
	const stageRef = useRef<HTMLDivElement | null>(null);
	const visible = usePageVisible();
	const inView = useMostlyInView(stageRef);
	const focusInside = useFocusWithin(stageRef);
	const focusHolds = pauseOnFocus && focusInside;
	const running = playing && !reduced && visible && inView && !focusHolds;

	const elapsed = useRef(0);
	const progress = useMotionValue(0);
	const overall = useMotionValue(count > 0 ? 1 / count : 0);
	const indexRef = useRef(0);
	const setProgress = useCallback(
		(p: number) => {
			progress.set(p);
			overall.set(count > 0 ? (indexRef.current + p) / count : 0);
		},
		[progress, overall, count],
	);
	const resetClock = useCallback(() => {
		elapsed.current = 0;
		setProgress(0);
	}, [setProgress]);

	// Reduced motion turning on mid-play stops the clock; it never restarts itself.
	useEffect(() => {
		if (reduced) setPlaying(false);
	}, [reduced]);

	const atEnd = index >= count - 1;
	// Seeking writes the ref before the state, so the clock never reads a stale beat.
	useEffect(() => {
		indexRef.current = index;
		if (elapsed.current === 0) setProgress(0);
	}, [index, setProgress]);

	useAnimationFrame((_, delta) => {
		if (!running) return;
		const beat = script.beats[indexRef.current];
		if (!beat) return;
		// Capped, so a long frame (a busy main thread) never skips a beat.
		elapsed.current += Math.min(delta, 100);
		setProgress(Math.min(1, elapsed.current / beat.durationMs));
		if (elapsed.current < beat.durationMs) return;
		resetClock();
		if (indexRef.current < count - 1) {
			indexRef.current += 1;
			setProgress(0);
			setIndex(indexRef.current);
		} else if (loop) {
			indexRef.current = clampBeat(script, loopFrom);
			setProgress(0);
			setIndex(indexRef.current);
		} else {
			setProgress(1);
			setPlaying(false);
		}
	});

	const seek = useCallback(
		(target: number | string) => {
			const i = typeof target === 'number' ? target : beatIndexOf(script, target);
			if (i < 0 && typeof target === 'string') return;
			const nextIndex = clampBeat(script, i);
			indexRef.current = nextIndex;
			resetClock();
			setIndex(nextIndex);
		},
		[script, resetClock],
	);
	const next = useCallback(() => seek(indexRef.current + 1), [seek]);
	const prev = useCallback(() => seek(indexRef.current - 1), [seek]);
	const play = useCallback(() => {
		if (reduced) return;
		// Play from the end replays from the start.
		if (indexRef.current >= count - 1) seek(0);
		setPlaying(true);
	}, [reduced, count, seek]);
	const pause = useCallback(() => setPlaying(false), []);
	const toggle = useCallback(() => (playing ? pause() : play()), [playing, pause, play]);
	const restart = useCallback(() => {
		seek(0);
		if (!reduced) setPlaying(true);
	}, [seek, reduced]);

	return {
		state: frames[index] ?? script.initial,
		index,
		beat: script.beats[index],
		count,
		running,
		playing,
		reduced,
		atEnd,
		play,
		pause,
		toggle,
		seek,
		next,
		prev,
		restart,
		progress,
		overall,
		stageRef,
	};
}
