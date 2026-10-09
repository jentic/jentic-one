/**
 * useResizableWidth — a persisted, clamped width for a docked side panel,
 * driven by a `ResizeHandle`.
 *
 * The width lives in a CSS custom property on the layout container (e.g.
 * `--ws-dock-w` on the Library's grid), so the container's own responsive
 * default stays in CSS and a drag never re-renders the page: `preview` writes
 * the property directly (call it from a rAF), `commit` stores it.
 *
 *   - Bounds: `min`, and `maxFor(containerWidth)` re-evaluated whenever the
 *     container resizes (viewport, Activity rail open/collapsed), so a stored
 *     width that no longer fits is clamped — never applied as-is.
 *   - Persistence: localStorage under `storageKey` (px). `reset` clears it and
 *     hands the width back to the responsive CSS default.
 *   - `width` is the applied value: the stored one, clamped, or (no stored
 *     value) the panel's measured default width.
 */
import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react';
import { readLocalPreference, writeLocalPreference } from '@/shared/lib/localPreference';

function readWidth(key: string): number | null {
	const raw = readLocalPreference(key);
	const n = raw == null ? NaN : Number(raw);
	return Number.isFinite(n) && n > 0 ? n : null;
}

function writeWidth(key: string, value: number | null) {
	writeLocalPreference(key, value == null ? null : String(Math.round(value)));
}

export interface UseResizableWidthOptions {
	/** localStorage key, e.g. `library.workspaceWidth`. */
	storageKey: string;
	/** The element carrying `cssVar` (and whose width bounds the max). */
	containerRef: RefObject<HTMLElement | null>;
	/** The resized panel — measured for the default width. */
	panelRef: RefObject<HTMLElement | null>;
	/** Custom property the container's layout reads, e.g. `--ws-dock-w`. */
	cssVar: `--${string}`;
	/** Smallest width (px). */
	min: number;
	/** Largest width (px) for a container this wide. Never below `min`. */
	maxFor: (containerWidth: number) => number;
	/** Off (e.g. below the breakpoint where the panel docks): nothing is applied. */
	enabled?: boolean;
}

export interface ResizableWidth {
	width: number;
	min: number;
	max: number;
	/** A stored (user-chosen) width is in effect. */
	isCustom: boolean;
	/** Apply a width live (no state update, no storage); returns it clamped. */
	preview: (px: number) => number;
	/** Apply and persist a width; returns it clamped. */
	commit: (px: number) => number;
	/** Forget the stored width — back to the responsive default. */
	reset: () => void;
}

export function useResizableWidth({
	storageKey,
	containerRef,
	panelRef,
	cssVar,
	min,
	maxFor,
	enabled = true,
}: UseResizableWidthOptions): ResizableWidth {
	const [stored, setStored] = useState<number | null>(() => readWidth(storageKey));
	const [containerWidth, setContainerWidth] = useState(0);
	const [measured, setMeasured] = useState(0);

	const max = Math.max(min, containerWidth > 0 ? maxFor(containerWidth) : Infinity);
	const clamp = useCallback(
		(px: number) => Math.round(Math.min(max, Math.max(min, px))),
		[min, max],
	);

	// Track the container (bounds) and the panel (the default width).
	useLayoutEffect(() => {
		if (!enabled) return;
		const container = containerRef.current;
		const panel = panelRef.current;
		const measure = () => {
			if (container) setContainerWidth(container.getBoundingClientRect().width);
			if (panel) setMeasured(panel.getBoundingClientRect().width);
		};
		measure();
		const observer = new ResizeObserver(measure);
		if (container) observer.observe(container);
		if (panel) observer.observe(panel);
		return () => observer.disconnect();
	}, [enabled, containerRef, panelRef]);

	// Apply the stored width (clamped to what fits now), or hand back to CSS.
	useLayoutEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		if (enabled && stored != null) container.style.setProperty(cssVar, `${clamp(stored)}px`);
		else container.style.removeProperty(cssVar);
	}, [enabled, stored, clamp, cssVar, containerRef]);

	useEffect(
		() => () => {
			containerRef.current?.style.removeProperty(cssVar);
		},
		[containerRef, cssVar],
	);

	const preview = useCallback(
		(px: number) => {
			const next = clamp(px);
			containerRef.current?.style.setProperty(cssVar, `${next}px`);
			return next;
		},
		[clamp, containerRef, cssVar],
	);

	const commit = useCallback(
		(px: number) => {
			const next = clamp(px);
			containerRef.current?.style.setProperty(cssVar, `${next}px`);
			setStored(next);
			writeWidth(storageKey, next);
			return next;
		},
		[clamp, containerRef, cssVar, storageKey],
	);

	const reset = useCallback(() => {
		containerRef.current?.style.removeProperty(cssVar);
		setStored(null);
		writeWidth(storageKey, null);
	}, [containerRef, cssVar, storageKey]);

	const width = stored != null ? clamp(stored) : clamp(measured || min);
	return {
		width,
		min,
		max: Number.isFinite(max) ? Math.round(max) : width,
		isCustom: stored != null,
		preview,
		commit,
		reset,
	};
}
