/**
 * RefreshControl — how fresh the Overview is, and a way to make it fresher.
 *
 *   ● Updated 12s ago  [⟳]
 *
 * The dot says auto-refresh is on and pings whenever new data lands; the
 * label rolls in on each update and then ages quietly ("just now" → "15s ago"
 * → "2m ago"). The button spins while a manual refresh is in flight, finishes
 * its turn instead of snapping back, and pops a check when it's done.
 *
 * Reduced motion keeps the information and drops the movement.
 */
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useAnimate, useReducedMotion } from 'framer-motion';
import { Check, RefreshCw } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

/** Keep the spin on screen long enough to read, even for a cached reply. */
const MIN_SPIN_MS = 650;
const DONE_MS = 1_100;

export function formatAge(ms: number): string {
	const s = Math.max(0, Math.floor(ms / 1000));
	if (s < 10) return 'just now';
	if (s < 60) return `${Math.floor(s / 5) * 5}s ago`;
	const m = Math.floor(s / 60);
	return m < 60 ? `${m}m ago` : `${Math.floor(m / 60)}h ago`;
}

/** "Now", re-read every few seconds so the age label keeps up. */
function useNow(stepMs: number): number {
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		const id = setInterval(() => setNow(Date.now()), stepMs);
		return () => clearInterval(id);
	}, [stepMs]);
	return now;
}

export interface RefreshControlProps {
	/** Epoch ms the data on screen was fetched; 0 before the first load. */
	updatedAt: number;
	onRefresh: () => Promise<unknown>;
	/** Auto-refresh cadence, for the tooltip. */
	intervalMs: number;
	/** What's being refreshed, for the button's accessible name. */
	subject?: string;
	className?: string;
}

export function RefreshControl({
	updatedAt,
	onRefresh,
	intervalMs,
	subject = 'usage',
	className,
}: RefreshControlProps) {
	const reduce = useReducedMotion();
	const now = useNow(5_000);
	const [phase, setPhase] = useState<'idle' | 'spinning' | 'done'>('idle');
	const [announce, setAnnounce] = useState('');
	const [scope, animate] = useAnimate<HTMLSpanElement>();
	const turns = useRef(0);

	// While spinning, loop whole turns; on stop, glide to the next full turn so
	// the arrow never snaps back mid-rotation.
	useEffect(() => {
		if (reduce || !scope.current) return;
		if (phase !== 'spinning') return;
		let alive = true;
		// The icon remounts at 0° after each check, so count turns afresh.
		turns.current = 0;
		const loop = async () => {
			while (alive) {
				turns.current += 1;
				await animate(
					scope.current,
					{ rotate: turns.current * 360 },
					{ duration: 0.7, ease: 'linear' },
				);
			}
		};
		void loop();
		return () => {
			alive = false;
		};
	}, [phase, reduce, animate, scope]);

	useEffect(() => {
		if (phase !== 'done') return;
		const t = setTimeout(() => setPhase('idle'), DONE_MS);
		return () => clearTimeout(t);
	}, [phase]);

	const refresh = async () => {
		if (phase === 'spinning') return;
		setPhase('spinning');
		setAnnounce('');
		const started = Date.now();
		try {
			await onRefresh();
		} finally {
			const wait = MIN_SPIN_MS - (Date.now() - started);
			if (wait > 0) await new Promise((r) => setTimeout(r, wait));
			setPhase('done');
			setAnnounce(`${subject[0].toUpperCase()}${subject.slice(1)} refreshed.`);
		}
	};

	const age = updatedAt ? formatAge(now - updatedAt) : 'Loading…';
	const seconds = Math.round(intervalMs / 1000);

	return (
		<div className={cn('flex items-center gap-2', className)}>
			<p
				className="text-muted-foreground flex items-center gap-2 text-xs whitespace-nowrap"
				title={`Refreshes automatically every ${seconds}s while this tab is open`}
			>
				<span className="relative flex h-2 w-2" aria-hidden="true">
					{!reduce && updatedAt > 0 && (
						// Re-keyed per update: one ping each time fresh data lands.
						<motion.span
							key={updatedAt}
							className="bg-success absolute inset-0 rounded-full"
							initial={{ opacity: 0.7, scale: 1 }}
							animate={{ opacity: 0, scale: 2.6 }}
							transition={{ duration: 1.1, ease: 'easeOut' }}
						/>
					)}
					<span className="bg-success/80 relative inline-flex h-2 w-2 rounded-full" />
				</span>
				<span className="sr-only">Auto-refresh on. </span>
				<span className="inline-flex overflow-hidden">
					Updated&nbsp;
					<AnimatePresence mode="popLayout" initial={false}>
						<motion.span
							key={updatedAt}
							className="text-foreground/80 inline-block tabular-nums"
							initial={reduce ? false : { y: 10, opacity: 0 }}
							animate={{ y: 0, opacity: 1 }}
							exit={reduce ? undefined : { y: -10, opacity: 0 }}
							transition={{ type: 'spring', stiffness: 420, damping: 32 }}
						>
							{age}
						</motion.span>
					</AnimatePresence>
				</span>
			</p>

			<button
				type="button"
				onClick={() => void refresh()}
				aria-label={`Refresh ${subject}`}
				aria-busy={phase === 'spinning'}
				title={`Refresh now (auto every ${seconds}s)`}
				className={cn(
					'border-border bg-muted/50 text-muted-foreground relative inline-flex h-[1.875rem] w-[1.875rem] shrink-0 items-center justify-center rounded-lg border transition-colors',
					'hover:text-foreground hover:bg-muted focus-visible:ring-ring/60 focus-visible:ring-2 focus-visible:outline-none',
					phase === 'spinning' && 'text-primary',
					phase === 'done' && 'border-success/50 text-success',
				)}
			>
				<AnimatePresence mode="wait" initial={false}>
					{phase === 'done' ? (
						<motion.span
							key="done"
							className="inline-flex"
							initial={reduce ? false : { scale: 0.4, opacity: 0 }}
							animate={{ scale: 1, opacity: 1 }}
							exit={reduce ? undefined : { scale: 0.6, opacity: 0 }}
							transition={{ type: 'spring', stiffness: 520, damping: 22 }}
						>
							<Check className="h-3.5 w-3.5" aria-hidden="true" />
						</motion.span>
					) : (
						<motion.span
							key="icon"
							className="inline-flex"
							initial={reduce ? false : { scale: 0.6, opacity: 0 }}
							animate={{ scale: 1, opacity: 1 }}
							exit={reduce ? undefined : { scale: 0.6, opacity: 0 }}
							transition={{ duration: 0.15 }}
						>
							<span ref={scope} className="inline-flex">
								<RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
							</span>
						</motion.span>
					)}
				</AnimatePresence>
			</button>
			<span className="sr-only" role="status" aria-live="polite">
				{announce}
			</span>
		</div>
	);
}
