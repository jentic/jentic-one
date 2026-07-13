/**
 * CodeBlock — a terminal-styled, copyable command/code snippet for the docs
 * narrative (Install / Quickstart pages). Mono font, dark surface, a copy
 * affordance in the corner, and an optional caption above.
 *
 * Pure presentational; the value is copied verbatim (so multi-line shell
 * blocks paste cleanly). For inline single tokens use a plain <code> instead.
 */
import { CopyButton } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

export interface CodeBlockProps {
	/** The exact text to render and copy. */
	code: string;
	/** Optional small label shown above the block (e.g. "Terminal", "bash"). */
	caption?: string;
	/** Render a `$` prompt before each line (shell snippets). Defaults to false. */
	prompt?: boolean;
	className?: string;
}

export function CodeBlock({ code, caption, prompt = false, className }: CodeBlockProps) {
	const body = code.replace(/\n$/, '');
	const rendered = prompt
		? body
				.split('\n')
				.map((line) =>
					line.trim().startsWith('#') || line.trim() === '' ? line : `$ ${line}`,
				)
				.join('\n')
		: body;

	return (
		<div
			className={cn('border-border overflow-hidden rounded-lg border bg-black/30', className)}
		>
			{caption && (
				<div className="border-border/60 text-foreground/45 flex items-center justify-between border-b px-3 py-1.5 text-[11px] font-medium tracking-wide uppercase">
					<span>{caption}</span>
				</div>
			)}
			<div className="relative">
				<pre
					tabIndex={0}
					aria-label={caption ? `${caption} code snippet` : 'Code snippet'}
					className="text-foreground/90 overflow-x-auto p-3 pr-12 font-mono text-[13px] leading-relaxed"
				>
					{rendered}
				</pre>
				<div className="absolute top-2 right-2">
					<CopyButton value={body} size="icon" variant="ghost" />
				</div>
			</div>
		</div>
	);
}
