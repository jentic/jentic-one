import { CopyButton } from '@/shared/ui/CopyButton';
import { cn } from '@/shared/lib/utils';

export interface CodeSnippetProps {
	/** The copy-paste payload — rendered verbatim and fed to the copy button. */
	code: string;
	/** Optional tiny uppercase caption above the block (eyebrow style). */
	label?: string;
	className?: string;
}

/**
 * One copyable code block: a bordered mono `<pre>` with a corner CopyButton
 * and an optional eyebrow label. The shared chrome for CLI snippets and
 * client-config JSON (the MCP config card; DcrQuickstart renders the same
 * chrome and migrates here as a follow-up).
 */
export function CodeSnippet({ code, label, className }: CodeSnippetProps) {
	return (
		<div className={cn(className)}>
			{label && (
				<p className="text-muted-foreground/70 mb-1 text-[10px] tracking-wider uppercase">
					{label}
				</p>
			)}
			<div className="bg-muted/60 border-border/60 relative rounded-lg border p-3">
				<pre className="text-foreground/90 overflow-x-auto pr-8 font-mono text-xs leading-relaxed">
					{code}
				</pre>
				<div className="absolute top-2 right-2">
					<CopyButton value={code} />
				</div>
			</div>
		</div>
	);
}
