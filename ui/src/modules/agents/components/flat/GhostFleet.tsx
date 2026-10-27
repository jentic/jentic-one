/**
 * GhostFleet — the zero-agents preview of the fleet view, drawn as an annotated
 * illustration rather than a skeleton, so it reads as "nothing here yet", not
 * "still loading". Purely decorative: the root is `aria-hidden`.
 */
import type { ReactNode, RefObject } from 'react';
import { motion } from 'framer-motion';
import {
	Activity,
	Bot,
	Clock,
	KeyRound,
	LibraryBig,
	Lock,
	Plug,
	Plus,
	ShieldCheck,
	Unplug,
} from 'lucide-react';
import { STATUS_ICON, STATUS_TINT } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

/** The shared soft ease (`--ease-out-soft`), for framer-motion. */
export const EASE_OUT_SOFT = [0.22, 1, 0.36, 1] as const;

export const GHOST_CAPTION_TITLE = 'Nothing here yet';
export const GHOST_CAPTION_SUB = 'you have no agents — this is where they will appear';

const SLOT = 'border-hairline-field shrink-0 border-[1.5px] border-dashed';

function GhostChip({ icon: Icon, children }: { icon: Icon; children: ReactNode }) {
	return (
		<span className="bg-surface-tonal/60 text-foreground-faint inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-semibold">
			<Icon className="size-3" />
			{children}
		</span>
	);
}

type Icon = (p: { className?: string }) => ReactNode;

const ROWS: [Icon, string, Icon, string][] = [
	[LibraryBig, 'APIs it can call', Plug, 'Not connected'],
	[KeyRound, 'Credentials', Lock, 'None bound'],
	[ShieldCheck, 'Permissions', Lock, 'Set on approval'],
	[Activity, 'Activity', Clock, 'No calls yet'],
];

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
	const StatusIcon = arrived ? STATUS_ICON[arrived.status] : null;

	return (
		<div
			aria-hidden="true"
			// Skipped by `checkA11y`'s contrast audit (see `test-utils.tsx`): its half-opacity
			// labels are deliberately backdrop to the card in front.
			data-a11y-decorative=""
			data-testid="ghost-fleet"
			className="pointer-events-none pt-0.5 select-none"
		>
			<div
				data-testid="ghost-frame"
				className="border-annotation-edge bg-surface-annotation rounded-[18px] [border-width:1.5px] border-dashed [mask-image:linear-gradient(180deg,#000_0%,#000_72%,transparent_100%)]"
			>
				{/* Outside the faded layer, so the caption keeps full token contrast. */}
				<div
					data-testid="ghost-caption"
					className="border-hairline-field flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 border-b-[1.5px] border-dashed px-5 pt-[11px] pb-2.5"
				>
					<span className="font-heading text-foreground-sub text-[22px] leading-7 font-semibold tracking-[-0.01em]">
						{GHOST_CAPTION_TITLE}
					</span>
					<span className="text-foreground-faint text-[14px] leading-6">
						{GHOST_CAPTION_SUB}
					</span>
				</div>
				{/* The clear band the landing card's top edge lands in. */}
				<div className="px-3.5 pt-11">
					{/* Plain dimming, no blur: a blur reads as content loading behind a modal. */}
					<div
						data-testid="ghost-drawing"
						className="[mask-image:linear-gradient(180deg,#000_0%,#000_66%,transparent_100%)] opacity-50 [filter:saturate(0.25)]"
					>
						<div className="bg-surface-1 flex min-h-11 items-center gap-0.5 overflow-hidden rounded-xl p-1">
							<motion.span
								ref={slotRef}
								data-testid="ghost-tab"
								data-arrived={arrived != null}
								data-status={arrived?.status}
								// Keyed on the status, so the approval pops the tab too.
								key={arrived?.status ?? 'empty'}
								animate={
									arrived != null && !reducedMotion && !settled
										? { scale: [0.96, 1.04, 1] }
										: { scale: 1 }
								}
								transition={{ duration: 0.52, ease: EASE_OUT_SOFT }}
								className="bg-surface-tonal/80 text-foreground-sub flex h-9 shrink-0 items-center gap-2 rounded-[9px] pr-3 pl-1.5 text-[13px] font-semibold"
							>
								{arrived != null && StatusIcon ? (
									<>
										<StatusIcon
											className={cn('size-3.5', STATUS_TINT[arrived.status])}
										/>
										<span>{arrived.name}</span>
									</>
								) : (
									<>
										<span
											className={cn(
												SLOT,
												'grid size-5 place-items-center rounded-full',
											)}
										>
											<Plus className="text-foreground-faint size-3" />
										</span>
										Your first agent
									</>
								)}
							</motion.span>
							{[0.6, 0.4, 0.22].map((o) => (
								<span
									key={o}
									style={{ opacity: o }}
									className="flex h-9 shrink-0 items-center px-2"
								>
									<span className={cn(SLOT, 'size-5 rounded-full')} />
								</span>
							))}
							<span className="text-foreground-faint mr-2 ml-auto hidden items-center gap-1.5 text-xs font-medium sm:inline-flex">
								<Unplug className="size-3.5" />
								Nothing connected yet
							</span>
						</div>
						<div className="bg-surface-1 rounded-panel mt-3 flex items-center gap-4 px-5 py-4">
							<span
								className={cn(SLOT, 'grid size-10 place-items-center rounded-full')}
							>
								<Bot className="text-foreground-faint size-[18px]" />
							</span>
							<span className="grid min-w-0 gap-0.5">
								<span className="font-heading text-foreground-sub text-[15px] font-semibold tracking-[-0.01em]">
									Your first agent
								</span>
								<span className="text-foreground-faint truncate text-[12.5px]">
									Not registered yet · signs in with its own key · waits here for
									your approval
								</span>
							</span>
							<span className="ml-auto hidden items-center gap-2 sm:flex">
								<GhostChip icon={Lock}>No access yet</GhostChip>
							</span>
						</div>
						<div className="relative mt-1">
							<span className="bg-hairline-field absolute top-0 bottom-6 left-[31px] w-px" />
							{ROWS.map(([RowIcon, title, ChipIcon, state]) => (
								<div
									key={title}
									className="relative flex items-center gap-3 py-2.5 pl-[65px]"
								>
									<span className="bg-hairline-field absolute top-1/2 left-[31px] h-px w-6" />
									<span
										className={cn(
											SLOT,
											'text-foreground-faint grid size-8 place-items-center rounded-lg',
										)}
									>
										<RowIcon className="size-4" />
									</span>
									<span className="text-foreground-sub truncate text-[13px] font-semibold">
										{title}
									</span>
									<span className="mr-5 ml-auto hidden sm:inline-flex">
										<GhostChip icon={ChipIcon}>{state}</GhostChip>
									</span>
								</div>
							))}
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
