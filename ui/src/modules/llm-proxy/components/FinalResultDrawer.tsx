/**
 * FinalResultDrawer — the run's closing synthesis, verbatim.
 *
 * Opened from either the far-right Result node on the trace-flow canvas or the
 * pinned Outcome bar. Renders the whole `final_output.summary` text in a
 * readable <pre>-style block that preserves line breaks and markdown-ish
 * formatting; no artifact extraction. Falls back to an empty-state when the
 * session recorded no final output.
 */
import { CircleCheck, X } from 'lucide-react';
import { SheetPrimitive, CopyButton, Markdown } from '@/shared/ui';
import type { FinalOutput } from '@/modules/llm-proxy/api';

interface FinalResultDrawerProps {
	finalOutput: FinalOutput | null;
	open: boolean;
	onClose: () => void;
	/** Portal target — the fullscreen element when the canvas is fullscreen. */
	container?: HTMLElement | null;
}

export function FinalResultDrawer({
	finalOutput,
	open,
	onClose,
	container,
}: FinalResultDrawerProps) {
	const summary = finalOutput?.summary?.trim() ?? '';
	const hasOutput = summary.length > 0;

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabel="Run final output"
			className="sm:w-[600px]"
			container={container}
		>
			<div className="flex h-full flex-col">
				<div className="border-border/60 flex items-start justify-between gap-3 border-b px-5 py-4">
					<div className="min-w-0">
						<div className="text-accent-green mb-1.5 flex items-center gap-1.5">
							<CircleCheck className="h-4 w-4" />
							<span className="text-[10px] font-semibold tracking-wide uppercase">
								Result
							</span>
						</div>
						<h2 className="text-foreground text-sm font-semibold">Final output</h2>
						<p className="text-muted-foreground mt-0.5 text-xs">
							The run&rsquo;s closing synthesis — the deliverable the flow converges
							to.
						</p>
					</div>
					<div className="flex shrink-0 items-center gap-1">
						{hasOutput && (
							<CopyButton
								value={summary}
								variant="ghost"
								size="icon"
								ariaLabel="Copy final output"
							/>
						)}
						<button
							type="button"
							onClick={onClose}
							aria-label="Close"
							className="text-muted-foreground hover:text-foreground rounded p-1 transition-colors"
						>
							<X className="h-4 w-4" />
						</button>
					</div>
				</div>

				<div className="flex-1 overflow-y-auto px-5 py-4">
					{hasOutput ? (
						<Markdown source={summary} className="max-w-none" />
					) : (
						<div className="border-border/50 text-muted-foreground rounded-lg border border-dashed px-4 py-8 text-center text-sm">
							No final output recorded for this run.
						</div>
					)}
				</div>
			</div>
		</SheetPrimitive>
	);
}
