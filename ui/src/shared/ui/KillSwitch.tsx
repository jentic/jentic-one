/**
 * KillSwitch — the console-standard suspend/restore toggle: a Power pill
 * showing the entity's live state, with a two-step inline confirm (click to
 * arm, click again to apply) so the destructive flip never fires on a single
 * mis-click. Grown on the toolkit console; agent and service-account headers
 * render the same control wired to their own lifecycle mutations.
 *
 * Purely presentational: the caller owns the mutation and passes `pending`
 * back in, so the pill can show its spinner while the flip is in flight.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Ban, Power, ShieldCheck } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { cn } from '@/shared/lib/utils';

export interface KillSwitchProps {
	/** Current state — the pill mirrors it ("Active" vs the suspended label). */
	active: boolean;
	/** True while the caller's mutation is in flight (drives the spinner). */
	pending?: boolean;
	/** Apply the flip — called with the DESIRED state after inline confirm. */
	onToggle: (nextActive: boolean) => void;
	/** Pill text when active. */
	activeLabel?: string;
	/** Pill text when suspended (e.g. "Suspended", "Disabled"). */
	inactiveLabel?: string;
	/** Accessible name for the pill while active (e.g. "Suspend toolkit (kill switch)"). */
	suspendAriaLabel: string;
	/** Accessible name for the pill while suspended (e.g. "Restore toolkit access"). */
	restoreAriaLabel: string;
	/** Inline confirm question when suspending (e.g. "Block keys + agents?"). */
	suspendPrompt: string;
	/** Inline confirm question when restoring. */
	restorePrompt?: string;
	/** Confirm button label when suspending. */
	suspendConfirmLabel?: string;
	/** Confirm button label when restoring. */
	restoreConfirmLabel?: string;
	className?: string;
	'data-testid'?: string;
}

export function KillSwitch({
	active,
	pending = false,
	onToggle,
	activeLabel = 'Active',
	inactiveLabel = 'Suspended',
	suspendAriaLabel,
	restoreAriaLabel,
	suspendPrompt,
	restorePrompt = 'Restore access?',
	suspendConfirmLabel = 'Kill',
	restoreConfirmLabel = 'Restore',
	className,
	'data-testid': dataTestId,
}: KillSwitchProps) {
	const [confirming, setConfirming] = useState(false);
	const confirmId = useId();
	const confirmRef = useRef<HTMLButtonElement>(null);
	const pillRef = useRef<HTMLButtonElement>(null);

	useEffect(() => {
		if (confirming) confirmRef.current?.focus();
	}, [confirming]);

	/** Close the confirm group and hand focus back to the pill. */
	const dismiss = () => {
		setConfirming(false);
		pillRef.current?.focus();
	};

	const apply = () => {
		dismiss();
		onToggle(!active);
	};

	return (
		<div
			className={cn('inline-flex items-center gap-2', className)}
			data-testid={dataTestId}
			onKeyDown={(e) => {
				if (confirming && e.key === 'Escape') {
					e.stopPropagation();
					dismiss();
				}
			}}
		>
			<Button
				ref={pillRef}
				variant="ghost"
				loading={pending}
				onClick={() => setConfirming((c) => !c)}
				disabled={pending}
				aria-pressed={active}
				aria-expanded={confirming}
				aria-controls={confirming ? confirmId : undefined}
				aria-label={active ? suspendAriaLabel : restoreAriaLabel}
				className={cn(
					'group relative h-8 gap-2 rounded-full px-3 text-xs font-medium',
					active
						? 'bg-success/10 text-success border-success/30 hover:bg-success/20 border'
						: 'bg-danger/10 text-danger border-danger/30 hover:bg-danger/20 border',
				)}
			>
				{!pending && (
					<motion.span whileHover={{ scale: 1.1 }} className="flex items-center">
						{active ? (
							<Power className="h-3.5 w-3.5" />
						) : (
							<Ban className="h-3.5 w-3.5" />
						)}
					</motion.span>
				)}
				<span>{active ? activeLabel : inactiveLabel}</span>
			</Button>

			{confirming && (
				<motion.div
					id={confirmId}
					role="group"
					aria-label={active ? suspendPrompt : restorePrompt}
					initial={{ opacity: 0, x: -4 }}
					animate={{ opacity: 1, x: 0 }}
					transition={{ duration: 0.15 }}
					className={cn(
						'inline-flex items-center gap-2 rounded-full border px-3 py-1',
						active ? 'border-danger/30 bg-danger/5' : 'border-success/30 bg-success/5',
					)}
				>
					<span className="text-muted-foreground text-xs">
						{active ? suspendPrompt : restorePrompt}
					</span>
					<Button
						ref={confirmRef}
						variant="ghost"
						onClick={apply}
						disabled={pending}
						className={cn(
							'gap-1 rounded-full px-2 py-0.5 text-xs font-semibold',
							active
								? 'bg-danger/15 text-danger hover:bg-danger/25'
								: 'bg-success/15 text-success hover:bg-success/25',
						)}
					>
						{active ? (
							<>
								<Ban className="h-3 w-3" /> {suspendConfirmLabel}
							</>
						) : (
							<>
								<ShieldCheck className="h-3 w-3" /> {restoreConfirmLabel}
							</>
						)}
					</Button>
					<Button
						variant="ghost"
						onClick={dismiss}
						className="text-muted-foreground hover:text-foreground px-1 py-0 text-xs"
					>
						Cancel
					</Button>
				</motion.div>
			)}
		</div>
	);
}
