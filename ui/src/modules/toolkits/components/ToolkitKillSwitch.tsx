import { useEffect, useId, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Ban, Power, ShieldCheck } from 'lucide-react';
import { Button } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { useSetToolkitActive } from '@/modules/toolkits/api';

/**
 * Toolkit-level kill switch — a Power-toggle pill that suspends or restores ALL
 * access for a toolkit by flipping its `active` flag, which the broker enforces
 * for both toolkit API keys and agent-identity callers. Click to arm an inline
 * confirm, click confirm to apply.
 */
export interface ToolkitKillSwitchProps {
	toolkitId: string;
	active: boolean;
	className?: string;
}

export function ToolkitKillSwitch({ toolkitId, active, className }: ToolkitKillSwitchProps) {
	const [confirming, setConfirming] = useState(false);
	const confirmId = useId();
	const confirmRef = useRef<HTMLButtonElement>(null);
	const setActive = useSetToolkitActive(toolkitId);

	useEffect(() => {
		if (confirming) confirmRef.current?.focus();
	}, [confirming]);

	const pending = setActive.isPending;

	const apply = () => {
		setActive.mutate(!active, { onSettled: () => setConfirming(false) });
	};

	return (
		<div className={cn('inline-flex items-center gap-2', className)}>
			<Button
				variant="ghost"
				loading={pending}
				onClick={() => setConfirming((c) => !c)}
				disabled={pending}
				aria-pressed={active}
				aria-expanded={confirming}
				aria-controls={confirming ? confirmId : undefined}
				aria-label={active ? 'Suspend toolkit (kill switch)' : 'Restore toolkit access'}
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
				<span>{active ? 'Active' : 'Suspended'}</span>
			</Button>

			{confirming && (
				<motion.div
					id={confirmId}
					role="group"
					aria-label={
						active ? 'Confirm suspending toolkit' : 'Confirm restoring toolkit access'
					}
					initial={{ opacity: 0, x: -4 }}
					animate={{ opacity: 1, x: 0 }}
					transition={{ duration: 0.15 }}
					className={cn(
						'inline-flex items-center gap-2 rounded-full border px-3 py-1',
						active ? 'border-danger/30 bg-danger/5' : 'border-success/30 bg-success/5',
					)}
				>
					<span className="text-muted-foreground text-xs">
						{active ? 'Block keys + agents?' : 'Restore access?'}
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
								<Ban className="h-3 w-3" /> Kill
							</>
						) : (
							<>
								<ShieldCheck className="h-3 w-3" /> Restore
							</>
						)}
					</Button>
					<Button
						variant="ghost"
						onClick={() => setConfirming(false)}
						className="text-muted-foreground hover:text-foreground px-1 py-0 text-xs"
					>
						Cancel
					</Button>
				</motion.div>
			)}
		</div>
	);
}
