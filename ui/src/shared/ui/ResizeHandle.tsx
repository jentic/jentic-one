/**
 * ResizeHandle — a draggable, keyboard-accessible vertical splitter between a
 * main column and a docked side panel (pair with `useResizableWidth`).
 *
 *   - Pointer: drag to resize (applied per animation frame through
 *     `onPreview`, committed once on release); double-click resets.
 *     While dragging, text selection is off and the cursor stays `col-resize`
 *     everywhere, so a fast drag that leaves the handle doesn't flicker.
 *   - Keyboard: focusable `role="separator"` with `aria-valuenow/min/max`;
 *     ←/→ move the divider by `step` (Shift = ×4), Home/End jump to the
 *     min/max width, Enter resets.
 *   - Look: a hairline with a grip centred on it — a small tonal pill with a
 *     2×2 dot pattern, always visible so it reads as draggable. The line and
 *     dots brighten on hover / drag; keyboard focus adds a ring round the
 *     grip. Centred on the handle, which the caller keeps viewport-tall and
 *     sticky, so the grip stays in the visible part of it. The hit area runs
 *     a few px past the handle's box. Only colour transitions (none under
 *     reduced motion); nothing changes size, so there's no layout shift.
 *
 * `panelSide` says where the panel is: with the panel on the `end` (right),
 * moving the divider left widens it.
 */
import {
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
	type KeyboardEvent,
	type PointerEvent,
} from 'react';
import { cn } from '@/shared/lib/utils';

/** The grip pill's size (px) — odd, so it centres on a 1px line exactly. */
const GRIP_W = 15;
const GRIP_H = 29;

export interface ResizeHandleProps {
	/** The panel's current width (px). */
	value: number;
	min: number;
	max: number;
	/** Live update during a drag; returns the clamped width actually applied. */
	onPreview: (px: number) => number;
	/** Final width (end of a drag, or a key press). */
	onCommit: (px: number) => void;
	/** Back to the default width (double-click / Enter). */
	onReset: () => void;
	/** Accessible name, e.g. "Resize workspace panel". */
	label: string;
	/** Which side of the handle the resized panel is on. Default `end`. */
	panelSide?: 'start' | 'end';
	/** Keyboard step (px). Default 16. */
	step?: number;
	className?: string;
	'data-testid'?: string;
}

export function ResizeHandle({
	value,
	min,
	max,
	onPreview,
	onCommit,
	onReset,
	label,
	panelSide = 'end',
	step = 16,
	className,
	'data-testid': testId = 'resize-handle',
}: ResizeHandleProps) {
	const [live, setLive] = useState<number | null>(null);
	const drag = useRef<{ startX: number; startWidth: number; x: number; frame: number } | null>(
		null,
	);
	const current = live ?? value;
	const sign = panelSide === 'end' ? -1 : 1;

	// Whole-pixel geometry (no transforms / half pixels, so the dots stay
	// crisp at any DPR): the line on a whole px at the handle's centre, the
	// grip centred on it and on the VISIBLE part of the handle — re-done on
	// resize and on any scroll (the handle is sticky / may run off-screen).
	const handleRef = useRef<HTMLDivElement>(null);
	const lineRef = useRef<HTMLSpanElement>(null);
	const gripRef = useRef<HTMLSpanElement>(null);
	useLayoutEffect(() => {
		const el = handleRef.current;
		const line = lineRef.current;
		const grip = gripRef.current;
		if (!el || !line || !grip) return;
		let frame = 0;
		const place = () => {
			frame = 0;
			const r = el.getBoundingClientRect();
			const lineX = Math.round(r.left + r.width / 2 - 0.5);
			const top = Math.max(r.top, 0);
			const bottom = Math.min(r.bottom, window.innerHeight);
			const mid = bottom > top ? (top + bottom) / 2 : r.top + r.height / 2;
			const gripY = Math.round(
				Math.min(Math.max(mid - GRIP_H / 2, r.top), r.bottom - GRIP_H),
			);
			line.style.left = `${lineX - r.left}px`;
			grip.style.left = `${lineX - (GRIP_W - 1) / 2 - r.left}px`;
			grip.style.top = `${gripY - r.top}px`;
		};
		const schedule = () => {
			if (!frame) frame = requestAnimationFrame(place);
		};
		place();
		const observer = new ResizeObserver(schedule);
		observer.observe(el);
		window.addEventListener('resize', schedule);
		document.addEventListener('scroll', schedule, { capture: true, passive: true });
		return () => {
			if (frame) cancelAnimationFrame(frame);
			observer.disconnect();
			window.removeEventListener('resize', schedule);
			document.removeEventListener('scroll', schedule, { capture: true });
		};
	}, []);

	// Restore the page if we unmount mid-drag.
	useEffect(
		() => () => {
			if (drag.current?.frame) cancelAnimationFrame(drag.current.frame);
			document.documentElement.classList.remove('is-resizing');
		},
		[],
	);

	const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
		if (event.button !== 0) return;
		event.preventDefault();
		event.currentTarget.setPointerCapture?.(event.pointerId);
		event.currentTarget.focus({ preventScroll: true });
		drag.current = { startX: event.clientX, startWidth: value, x: event.clientX, frame: 0 };
		setLive(value);
		// Global: no text selection, col-resize cursor everywhere (see index.css).
		document.documentElement.classList.add('is-resizing');
	};

	const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
		const d = drag.current;
		if (!d) return;
		d.x = event.clientX;
		if (d.frame) return;
		d.frame = requestAnimationFrame(() => {
			d.frame = 0;
			setLive(onPreview(d.startWidth + sign * (d.x - d.startX)));
		});
	};

	const endDrag = (event: PointerEvent<HTMLDivElement>) => {
		const d = drag.current;
		if (!d) return;
		if (d.frame) cancelAnimationFrame(d.frame);
		drag.current = null;
		if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
			event.currentTarget.releasePointerCapture(event.pointerId);
		}
		document.documentElement.classList.remove('is-resizing');
		const final = d.startWidth + sign * (d.x - d.startX);
		setLive(null);
		// A click without movement isn't a resize (and mustn't pin the default).
		if (d.x !== d.startX) onCommit(final);
	};

	const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
		const by = event.shiftKey ? step * 4 : step;
		let next: number | null = null;
		if (event.key === 'ArrowLeft') next = value - sign * by;
		else if (event.key === 'ArrowRight') next = value + sign * by;
		else if (event.key === 'Home') next = min;
		else if (event.key === 'End') next = max;
		else if (event.key === 'Enter') {
			event.preventDefault();
			onReset();
			return;
		}
		if (next == null) return;
		event.preventDefault();
		onCommit(Math.min(max, Math.max(min, next)));
	};

	return (
		<div
			ref={handleRef}
			role="separator"
			aria-orientation="vertical"
			aria-label={label}
			aria-valuenow={Math.round(current)}
			aria-valuemin={Math.round(min)}
			aria-valuemax={Math.round(max)}
			aria-valuetext={`${Math.round(current)} pixels wide`}
			tabIndex={0}
			title="Drag to resize · double-click to reset"
			data-testid={testId}
			data-dragging={live != null || undefined}
			onPointerDown={onPointerDown}
			onPointerMove={onPointerMove}
			onPointerUp={endDrag}
			onPointerCancel={endDrag}
			// Capture can be lost without a pointerup (the element re-renders
			// away, the OS steals the pointer): end the drag so the global
			// `is-resizing` cursor/selection lock never sticks.
			onLostPointerCapture={endDrag}
			onDoubleClick={onReset}
			onKeyDown={onKeyDown}
			className={cn(
				'group/resize relative cursor-col-resize touch-none outline-none select-none',
				// A slightly wider target than the box (it's mostly empty gutter).
				"before:absolute before:-inset-x-1 before:inset-y-0 before:content-['']",
				className,
			)}
		>
			<span
				ref={lineRef}
				aria-hidden="true"
				className={cn(
					'absolute inset-y-0 left-1/2 w-px bg-[hsl(var(--border)/0.5)] transition-colors duration-150 motion-reduce:transition-none',
					'group-hover/resize:bg-primary/50 group-focus-visible/resize:bg-primary/50 group-data-[dragging=true]/resize:bg-primary',
				)}
			/>
			{/* Pill: 15×29 border-box (1px border) around a 9×9 grid of four
			    3px dots with 3px gaps — odd sizes so the grid sits on whole
			    pixels, centred on the 1px line. Positioned in whole px by JS. */}
			<span
				ref={gripRef}
				aria-hidden="true"
				data-testid={`${testId}-grip`}
				className={cn(
					'absolute top-1/2 left-1/2 box-border grid h-[29px] w-[15px] grid-cols-[repeat(2,3px)] grid-rows-[repeat(2,3px)] place-content-center gap-[3px] rounded-full',
					'bg-surface-tonal border-hairline-field border shadow-sm',
					'transition-[background-color,border-color,box-shadow] duration-150 motion-reduce:transition-none',
					'group-hover/resize:bg-surface-tonal-hover group-hover/resize:border-primary/40 group-data-[dragging=true]/resize:border-primary/60',
					'group-focus-visible/resize:ring-ring group-focus-visible/resize:ring-offset-background group-focus-visible/resize:ring-2 group-focus-visible/resize:ring-offset-2',
				)}
			>
				{[0, 1, 2, 3].map((i) => (
					<span
						key={i}
						data-testid={`${testId}-dot`}
						className={cn(
							'bg-foreground-faint block size-[3px] shrink-0 rounded-full transition-colors duration-150 motion-reduce:transition-none',
							'group-hover/resize:bg-foreground-lighter group-focus-visible/resize:bg-foreground-lighter group-data-[dragging=true]/resize:bg-primary',
						)}
					/>
				))}
			</span>
		</div>
	);
}
