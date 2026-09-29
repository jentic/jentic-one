/**
 * LandingControls — the transport for a scripted act: play / pause, step back,
 * step forward, restart, a progress bar that follows the clock, and the one
 * `aria-live` caption (announced per beat, never per frame). The `compact`
 * variant is just the icon buttons, each named by a tooltip, for a bar that
 * places its own caption and progress.
 *
 * Under reduced motion there is no play button: the act is a set of annotated
 * stills, stepped with Previous / Next.
 */
import type { KeyboardEvent, ReactNode } from 'react';
import { motion, useTransform, type MotionValue } from 'framer-motion';
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw } from 'lucide-react';
import { Button, Tooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import type { LandingTimeline } from '@/modules/agents/components/landing/useLandingTimeline';

function IconControl({
	label,
	onClick,
	disabled,
	compact,
	children,
}: {
	label: string;
	onClick: () => void;
	disabled?: boolean;
	compact: boolean;
	children: ReactNode;
}) {
	const button = (
		<Button
			variant="ghost"
			size="icon"
			onClick={onClick}
			disabled={disabled}
			aria-label={label}
			className={compact ? 'h-7 w-7' : undefined}
		>
			{children}
		</Button>
	);
	return compact ? (
		<Tooltip content={label} interactiveChild>
			{button}
		</Tooltip>
	) : (
		button
	);
}

export function LandingControls<S>({
	timeline,
	noun,
	variant = 'full',
	showCaption = true,
}: {
	timeline: LandingTimeline<S>;
	/** What the act calls itself in button labels ("walkthrough", "tour"). */
	noun: string;
	/** `compact`: icon buttons only (no counter, bar or caption). */
	variant?: 'full' | 'compact';
	/** Render the caption here; off when the act places `LandingCaption` itself. */
	showCaption?: boolean;
}) {
	const { playing, reduced, index, count, atEnd, beat } = timeline;
	const compact = variant === 'compact';
	const playLabel = playing ? `Pause the ${noun}` : `Play the ${noun}`;
	const PlayIcon = playing ? Pause : Play;
	const steps = (
		<>
			<IconControl
				compact={compact}
				label="Previous step"
				onClick={timeline.prev}
				disabled={index === 0}
			>
				<ChevronLeft className="h-4 w-4" aria-hidden="true" />
			</IconControl>
			<IconControl
				compact={compact}
				label="Next step"
				onClick={timeline.next}
				disabled={atEnd}
			>
				<ChevronRight className="h-4 w-4" aria-hidden="true" />
			</IconControl>
		</>
	);
	const restart = (
		<IconControl compact={compact} label={`Restart the ${noun}`} onClick={timeline.restart}>
			<RotateCcw className={compact ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" />
		</IconControl>
	);

	if (compact) {
		return (
			<div className="flex shrink-0 items-center gap-0.5">
				{reduced ? (
					steps
				) : (
					<IconControl compact label={playLabel} onClick={timeline.toggle}>
						<PlayIcon className="h-4 w-4" aria-hidden="true" />
					</IconControl>
				)}
				{restart}
			</div>
		);
	}

	return (
		<div className="space-y-2">
			<div className="flex flex-wrap items-center gap-2">
				<div className="flex items-center gap-1">
					{!reduced && (
						<Button
							variant="secondary"
							size="sm"
							onClick={timeline.toggle}
							aria-label={playLabel}
						>
							<PlayIcon className="h-4 w-4" aria-hidden="true" />
							{playing ? 'Pause' : atEnd ? 'Replay' : 'Play'}
						</Button>
					)}
					{steps}
					{restart}
				</div>
				<span className="text-muted-foreground font-mono text-xs tabular-nums">
					{index + 1} / {count}
				</span>
				<div
					className="bg-border h-0.5 min-w-16 flex-1 overflow-hidden rounded-full"
					aria-hidden="true"
				>
					<motion.div
						className="bg-primary/60 h-full w-full origin-left"
						style={{ scaleX: timeline.overall }}
					/>
				</div>
			</div>
			{showCaption && <LandingCaption caption={beat?.caption} />}
		</div>
	);
}

/**
 * A bar that fills with the clock across a span of beats (one plan step, one
 * setup step): empty before `first`, full after `first + length - 1`.
 */
export function BeatSpanProgress({
	timeline,
	first,
	length,
	className,
}: {
	timeline: { overall: MotionValue<number>; count: number };
	first: number;
	length: number;
	/** Placement and colour; the bar is absolutely positioned. */
	className: string;
}) {
	const { overall, count } = timeline;
	const scaleX = useTransform(overall, (v) =>
		Math.max(0, Math.min(1, (v * count - first) / length)),
	);
	return (
		<motion.span
			aria-hidden="true"
			className={cn('absolute origin-left', className)}
			style={{ scaleX }}
		/>
	);
}

/** The act's one `aria-live` caption, announced per beat. */
export function LandingCaption({ caption, className }: { caption?: string; className?: string }) {
	return (
		<p
			aria-live="polite"
			data-testid="landing-caption"
			className={className ?? 'text-foreground min-h-10 text-sm leading-relaxed'}
		>
			{caption}
		</p>
	);
}

/**
 * Space toggles play while focus is inside an act but not on a control — a
 * scoped handler, not a global hotkey (the page already binds `n`, `a`, `/`).
 */
export function spaceToggles(toggle: () => void) {
	return (e: KeyboardEvent<HTMLElement>) => {
		if (e.key !== ' ') return;
		const target = e.target as HTMLElement;
		if (
			target.closest(
				'button, a, input, textarea, select, [role="tab"], [contenteditable="true"]',
			)
		)
			return;
		e.preventDefault();
		toggle();
	};
}
