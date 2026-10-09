/**
 * GhostFleet — the zero-agents preview of the fleet view: the agent strip, the
 * APIs band, a row of API tiles and the dock, drawn as faint tonal shapes in
 * the theme's own surfaces — no edges, no glow (and not loading skeletons:
 * they don't shimmer, because nothing is loading).
 *
 * Its first tab is the slot a self-registering agent lands in: it reads
 * "Waiting for your agent…", fills in with the agent's pending glyph and
 * name once one registers, and flips to its active glyph once approved.
 * Purely decorative: the live status line in the landing card is what
 * assistive tech hears.
 */
import { useEffect, useState, type RefObject } from 'react';
import { motion } from 'framer-motion';
import { Activity, Bot, KeyRound, Plus, Settings2, ShieldCheck } from 'lucide-react';
import { McpIcon, STATUS_ICON, STATUS_TINT, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

/** The shared soft ease (`--ease-out-soft`), for framer-motion. */
export const EASE_OUT_SOFT = [0.22, 1, 0.36, 1] as const;

/** When the preview's one real-looking tile warms up after mount. */
const FILL_DELAY_MS = 450;

/** A text-line placeholder: a faint tonal bar. */
function GhostLine({ className }: { className?: string }) {
	return <span className={cn('bg-surface-tonal/70 block h-2 rounded-full', className)} />;
}

/** The agent in the preview's first slot. */
interface GhostArrival {
	name: string;
	status: 'pending' | 'active';
}

interface GhostFleetProps {
	/** The agent that just registered, once one has. */
	arrived: GhostArrival | null;
	/** The agent slot — where the hand-off to the real strip starts from. */
	slotRef?: RefObject<HTMLElement | null>;
	reducedMotion: boolean;
	/** The arrival is the state the page loaded in, not news: don't pop the tab. */
	settled?: boolean;
}

export function GhostFleet({ arrived, slotRef, reducedMotion, settled = false }: GhostFleetProps) {
	const [filled, setFilled] = useState(reducedMotion);
	useEffect(() => {
		if (filled) return;
		const id = window.setTimeout(() => setFilled(true), FILL_DELAY_MS);
		return () => window.clearTimeout(id);
	}, [filled]);

	const StatusIcon = arrived ? STATUS_ICON[arrived.status] : null;

	return (
		<div
			aria-hidden="true"
			data-testid="ghost-fleet"
			className="pointer-events-none [mask-image:linear-gradient(180deg,#000_40%,transparent_100%)] select-none"
		>
			<div className="bg-surface-1/70 flex min-h-10 items-center gap-1 rounded-lg px-1.5 py-1">
				<motion.span
					ref={slotRef}
					data-testid="ghost-tab"
					data-arrived={arrived != null}
					data-status={arrived?.status}
					// Keyed on the status, so the approval pops the tab as the arrival did.
					key={arrived?.status ?? 'empty'}
					animate={
						arrived != null && !reducedMotion && !settled
							? { scale: [0.96, 1.04, 1] }
							: { scale: 1 }
					}
					transition={{ duration: 0.52, ease: EASE_OUT_SOFT }}
					className={cn(
						'inline-flex h-[30px] items-center gap-2 rounded-md px-3 text-[13px] font-semibold transition-[background-color,color] duration-[400ms] ease-(--ease-out-soft)',
						arrived == null
							? 'text-muted-foreground'
							: 'bg-surface-tonal text-foreground',
					)}
				>
					{arrived != null && StatusIcon ? (
						<>
							<StatusIcon className={cn('size-3.5', STATUS_TINT[arrived.status])} />
							<span>{arrived.name}</span>
						</>
					) : (
						<>
							<span className="bg-primary/60 animate-soft-pulse h-1.5 w-1.5 rounded-full motion-reduce:animate-none" />
							Waiting for your agent…
						</>
					)}
				</motion.span>
				<span className="inline-flex h-[30px] items-center rounded-md px-2.5">
					<Bot className="text-foreground-faint h-3.5 w-3.5 opacity-60" />
				</span>
			</div>

			<div className="mt-4 flex min-h-8 items-center justify-between gap-2">
				<span className="flex items-center gap-2">
					<GhostLine className="w-10" />
					<GhostLine className="w-3.5" />
				</span>
				<span className="bg-surface-1/70 block h-[30px] w-[92px] rounded-lg" />
			</div>

			<div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
				<div
					className={cn(
						'flex h-[150px] flex-col gap-3 rounded-lg p-4 transition-[background-color] duration-[800ms] ease-(--ease-out-soft)',
						filled ? 'bg-surface-1' : 'bg-surface-1/60',
					)}
				>
					<div className="flex items-center gap-3">
						<span className="grid h-7 w-7 shrink-0 place-items-center">
							<VendorMark
								slug="github"
								size="sm"
								className={cn(
									'transition-opacity duration-[800ms]',
									filled ? 'opacity-80' : 'opacity-35',
								)}
							/>
						</span>
						<span
							className={cn(
								'text-[13px] font-semibold transition-colors duration-[800ms]',
								filled ? 'text-foreground/70' : 'text-foreground-sub',
							)}
						>
							GitHub
						</span>
					</div>
					<div className="mt-auto grid gap-2 pt-3">
						<GhostLine className="w-3/5" />
						<GhostLine className="w-2/5" />
					</div>
				</div>
				<div className="bg-surface-1/60 hidden h-[150px] flex-col gap-3 rounded-lg p-4 sm:flex">
					<div className="flex items-center gap-3">
						<span className="bg-surface-tonal/70 h-7 w-7 shrink-0 rounded-md" />
						<GhostLine className="w-20" />
					</div>
					<div className="mt-auto grid gap-2 pt-3">
						<GhostLine className="w-[55%]" />
						<GhostLine className="w-[35%]" />
					</div>
				</div>
				<div className="bg-surface-1/60 text-foreground-faint hidden h-[150px] flex-col items-center justify-center gap-2 rounded-lg p-4 text-[13px] font-semibold lg:flex">
					<span className="bg-surface-tonal/70 grid h-7 w-7 place-items-center rounded-md">
						<Plus className="h-3.5 w-3.5" />
					</span>
					Add API
				</div>
			</div>

			<div className="bg-surface-1/60 text-foreground-faint/60 mx-auto mt-6 flex w-fit items-center gap-3.5 rounded-full px-4 py-2.5">
				<span className="bg-surface-tonal/70 block h-6 w-[84px] rounded-full" />
				<span className="bg-hairline-field h-[18px] w-px" />
				<KeyRound className="h-[18px] w-[18px]" />
				<ShieldCheck className="h-[18px] w-[18px]" />
				<Activity className="h-[18px] w-[18px]" />
				<McpIcon className="h-[18px] w-[18px]" />
				<Settings2 className="h-[18px] w-[18px]" />
			</div>
		</div>
	);
}
