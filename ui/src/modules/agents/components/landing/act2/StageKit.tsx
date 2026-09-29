/**
 * Act 2's stage kit: the browser-style frame the miniatures sit in, the
 * scripted cursor that glides to a `data-cursor` target and clicks, a mini
 * button that presses when clicked, and the typing caret. Motion is
 * transform / opacity only; reduced motion jumps.
 */
import { useEffect, useState, type ReactNode, type RefObject } from 'react';
import { motion } from 'framer-motion';
import { MousePointer2 } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { EASE_OUT_SOFT, type Pt } from '@/modules/agents/components/landing/motion';

/** How long a miniature's sheets and rows take to settle before the cursor re-measures. */
const CURSOR_SETTLE_MS = 380;

/** Where the cursor rests with no target, as a fraction of the stage. */
const CURSOR_REST = 0.85;

export function BrowserFrame({
	url,
	children,
	footer,
	className,
}: {
	url: string;
	children: ReactNode;
	footer?: ReactNode;
	className?: string;
}) {
	return (
		<div
			className={cn(
				'border-border bg-background overflow-hidden rounded-2xl border',
				className,
			)}
			data-testid="browser-frame"
		>
			<div className="border-border bg-card flex items-center gap-1.5 border-b px-3 py-2">
				{[0, 1, 2].map((dot) => (
					<span
						key={dot}
						className="bg-border h-2.5 w-2.5 rounded-full"
						aria-hidden="true"
					/>
				))}
				<span
					className="bg-background text-muted-foreground border-border ml-3 min-w-0 flex-1 truncate rounded-md border px-2.5 py-0.5 font-mono text-[11px]"
					data-testid="frame-url"
				>
					<span className="sr-only">Screen: </span>
					you.jentic.one/app{url}
				</span>
			</div>
			{children}
			{footer}
		</div>
	);
}

/**
 * The scripted pointer. It measures its target inside `stage` on each beat,
 * once the miniature has settled, and whenever the stage resizes, so the
 * miniature's own layout decides where it lands.
 */
export function StageCursor({
	stage,
	target,
	press,
	pressKey,
	reduced,
}: {
	stage: RefObject<HTMLElement | null>;
	target?: string;
	press?: boolean;
	/** Changes per beat, so a repeated click on the same target replays. */
	pressKey: string;
	reduced: boolean;
}) {
	const [point, setPoint] = useState<Pt | null>(null);
	useEffect(() => {
		const root = stage.current;
		if (!root) return undefined;
		function measure() {
			if (!root) return;
			const el = target ? root.querySelector<HTMLElement>(`[data-cursor="${target}"]`) : null;
			const box = root.getBoundingClientRect();
			if (!el) {
				setPoint((p) => p ?? { x: box.width * CURSOR_REST, y: box.height * CURSOR_REST });
				return;
			}
			const r = el.getBoundingClientRect();
			setPoint({ x: r.left - box.left + r.width / 2, y: r.top - box.top + r.height / 2 });
		}
		measure();
		// Sheets slide and rows arrive; measure again once they settle.
		const t = window.setTimeout(measure, CURSOR_SETTLE_MS);
		const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
		ro?.observe(root);
		return () => {
			window.clearTimeout(t);
			ro?.disconnect();
		};
	}, [stage, target, pressKey]);

	if (!point) return null;
	return (
		<motion.div
			aria-hidden="true"
			data-testid="stage-cursor"
			data-target={target ?? ''}
			className="pointer-events-none absolute top-0 left-0 z-20"
			initial={false}
			animate={{ x: point.x, y: point.y }}
			transition={reduced ? { duration: 0 } : { duration: 0.7, ease: EASE_OUT_SOFT }}
		>
			{press && !reduced && (
				<motion.span
					key={pressKey}
					className="border-primary absolute -top-3 -left-3 h-6 w-6 rounded-full border-2"
					initial={{ scale: 0.3, opacity: 0 }}
					animate={{ scale: [0.3, 0.3, 1.4], opacity: [0, 0.9, 0] }}
					transition={{ duration: 0.9, times: [0, 0.6, 1] }}
				/>
			)}
			<motion.span
				key={`p-${pressKey}`}
				className="block"
				animate={press && !reduced ? { scale: [1, 1, 0.8, 1] } : { scale: 1 }}
				transition={{ duration: 0.9, times: [0, 0.6, 0.75, 0.9] }}
			>
				<MousePointer2 className="text-foreground fill-background h-5 w-5 drop-shadow-md" />
			</motion.span>
		</motion.div>
	);
}

/** A mini button in a miniature: presses when the cursor clicks it. */
export function MiniButton({
	cursor,
	pressed,
	variant = 'primary',
	children,
	className,
}: {
	cursor?: string;
	pressed?: boolean;
	variant?: 'primary' | 'ghost' | 'done';
	children: ReactNode;
	className?: string;
}) {
	return (
		<span
			data-cursor={cursor}
			className={cn(
				'inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-semibold transition-[transform,background-color,color] duration-150',
				variant === 'primary' && 'bg-primary text-primary-foreground',
				variant === 'ghost' && 'border-border text-foreground border',
				variant === 'done' && 'bg-success/15 text-success',
				pressed && 'bg-success text-background scale-90',
				className,
			)}
		>
			{children}
		</span>
	);
}

/** The blinking block caret of a field or terminal being typed into. */
export function Caret({
	on = true,
	tone = 'primary',
	className,
}: {
	on?: boolean;
	tone?: 'primary' | 'success';
	className?: string;
}) {
	return on ? (
		<span
			className={cn(
				'ml-px inline-block h-3 w-1.5 animate-pulse align-[-2px]',
				tone === 'primary' ? 'bg-primary' : 'bg-success',
				className,
			)}
		/>
	) : null;
}
