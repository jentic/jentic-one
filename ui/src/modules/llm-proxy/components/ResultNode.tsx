/**
 * ResultNode — the convergent terminal node at the far right of the trace-flow.
 *
 * The tree fans OUT (root → subagents → their call chains); this node closes the
 * loop by giving the run a single visual destination. Every visible leaf agent's
 * tail feeds a connector into it (drawn by the shared connector overlay). It is
 * visually distinct (larger, accent-tinted) and clickable — opening the
 * FinalResultDrawer with the run's full closing synthesis.
 */
import { forwardRef } from 'react';
import { motion } from 'framer-motion';
import { CircleCheck } from 'lucide-react';
import type { FinalOutput } from '@/modules/llm-proxy/api';
import { cn } from '@/shared/lib/utils';

interface ResultNodeProps {
	finalOutput: FinalOutput | null;
	onOpen: () => void;
}

/** A one-line preview of the summary (first non-empty line, trimmed). */
export function previewOf(finalOutput: FinalOutput | null): string {
	const text = finalOutput?.summary?.trim() ?? '';
	if (!text) return 'No final output recorded';
	const firstLine = text.split('\n').find((l) => l.trim().length > 0) ?? text;
	return firstLine.trim();
}

export const ResultNode = forwardRef<HTMLDivElement, ResultNodeProps>(function ResultNode(
	{ finalOutput, onOpen },
	ref,
) {
	const hasOutput = Boolean(finalOutput?.summary?.trim());
	const preview = previewOf(finalOutput);

	return (
		<div ref={ref} className="relative z-10 flex shrink-0 items-center">
			<motion.button
				type="button"
				initial={{ opacity: 0, scale: 0.92 }}
				animate={{ opacity: 1, scale: 1 }}
				transition={{ duration: 0.24, ease: 'easeOut' }}
				whileHover={{ y: -2 }}
				onClick={onOpen}
				title={hasOutput ? 'Open the run\u2019s final output' : 'No final output recorded'}
				aria-label="Open the run's final output"
				className={cn(
					'group focus-visible:ring-ring/60 relative flex w-[220px] flex-col gap-1.5 rounded-xl border-2 px-4 py-3.5 text-left shadow-sm transition-colors focus-visible:ring-2 focus-visible:outline-none',
					hasOutput
						? 'border-accent-green/50 bg-accent-green/[0.08] hover:bg-accent-green/[0.14] hover:border-accent-green/70'
						: 'border-border/60 bg-muted/30 hover:bg-muted/50',
				)}
			>
				<div
					className={cn(
						'flex items-center gap-1.5',
						hasOutput ? 'text-accent-green' : 'text-muted-foreground',
					)}
				>
					<CircleCheck className="h-4 w-4 shrink-0" />
					<span className="text-[10px] font-semibold tracking-wide uppercase">
						Result
					</span>
				</div>
				<div className="text-foreground text-sm font-semibold">Final output</div>
				<p className="text-muted-foreground line-clamp-2 text-[11px] leading-snug">
					{preview}
				</p>
			</motion.button>
		</div>
	);
});
