import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Power, Loader2, Ban, ShieldCheck } from 'lucide-react';
import { api } from '@/api/client';
import { cn } from '@/lib/utils';

/**
 * Toolkit-level kill switch — a Power-toggle pill (ported from jentic-webapp's
 * ToolkitDock) that suspends or restores ALL API access for a toolkit.
 *
 * This is intentionally a top-level, toolkit-wide control: it flips
 * `toolkits.disabled`, which the broker enforces for both toolkit API keys and
 * agent-identity callers. It is NOT a per-key control, so it lives in the
 * detail header rather than inside the API Keys card.
 *
 * Interaction: click once to arm (shows an inline confirm), click confirm to
 * apply. Active = emerald "Active"; suspended = danger "Suspended".
 */
export interface ToolkitKillSwitchProps {
	toolkitId: string;
	disabled: boolean;
	className?: string;
}

export function ToolkitKillSwitch({ toolkitId, disabled, className }: ToolkitKillSwitchProps) {
	const queryClient = useQueryClient();
	const [confirming, setConfirming] = useState(false);

	const mutation = useMutation({
		mutationFn: (next: boolean) => api.updateToolkit(toolkitId, { disabled: next }),
		onSuccess: () => {
			queryClient.invalidateQueries({ queryKey: ['toolkit', toolkitId] });
			queryClient.invalidateQueries({ queryKey: ['toolkits'] });
			setConfirming(false);
		},
	});

	const pending = mutation.isPending;
	const active = !disabled;

	return (
		<div className={cn('inline-flex items-center gap-2', className)}>
			<button
				type="button"
				onClick={() => setConfirming((c) => !c)}
				disabled={pending}
				aria-pressed={active}
				aria-label={active ? 'Suspend toolkit (kill switch)' : 'Restore toolkit access'}
				className={cn(
					'group relative flex h-8 cursor-pointer items-center gap-2 rounded-full px-3 text-xs font-medium transition-all disabled:pointer-events-none disabled:opacity-50',
					active
						? 'bg-success/10 text-success border-success/30 hover:bg-success/20 border'
						: 'bg-danger/10 text-danger border-danger/30 hover:bg-danger/20 border',
				)}
			>
				{pending ? (
					<Loader2 className="h-3.5 w-3.5 animate-spin" />
				) : (
					<motion.span whileHover={{ scale: 1.1 }} className="flex items-center">
						{active ? (
							<Power className="h-3.5 w-3.5" />
						) : (
							<Ban className="h-3.5 w-3.5" />
						)}
					</motion.span>
				)}
				<span>{active ? 'Active' : 'Suspended'}</span>
			</button>

			{confirming && (
				<motion.div
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
					<button
						type="button"
						onClick={() => mutation.mutate(active)}
						disabled={pending}
						className={cn(
							'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold transition-colors disabled:opacity-50',
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
					</button>
					<button
						type="button"
						onClick={() => setConfirming(false)}
						className="text-muted-foreground hover:text-foreground text-xs"
					>
						Cancel
					</button>
				</motion.div>
			)}
		</div>
	);
}
