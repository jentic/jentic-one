/**
 * StatusGlyph — the one status vocabulary for log-style rows.
 *
 * ok       it worked (a completed call, a finished job)
 * fail     it didn't (failed call/job, error event, denied request)
 * warn     worth a look (warnings, cancellations, waiting for a person)
 * running  still going (queued / running jobs)
 * neutral  a plain record (most audit entries, informational events)
 *
 * Weight follows importance: the everyday outcomes are quiet dots, the ones
 * that need a reader's eye are glyphs. The label is screen-reader text, so the
 * tone is never carried by colour alone. Without a label (or with an empty one)
 * the glyph is decorative and hidden from assistive tech — use that only where
 * adjacent text already says the status.
 */
import { AlertTriangle, Loader2, XCircle } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

export type StatusGlyphTone = 'ok' | 'fail' | 'warn' | 'running' | 'neutral';

export interface StatusGlyphProps {
	tone: StatusGlyphTone;
	/** Screen-reader word for the tone; omit when adjacent text already says it. */
	label?: string;
	className?: string;
}

export function StatusGlyph({ tone, label, className }: StatusGlyphProps) {
	return (
		<span
			data-tone={tone}
			aria-hidden={label ? undefined : true}
			className={cn('flex h-5 w-4 shrink-0 items-center justify-center', className)}
		>
			{tone === 'fail' ? (
				<XCircle className="text-danger h-4 w-4" aria-hidden="true" />
			) : tone === 'warn' ? (
				<AlertTriangle className="text-warning h-3.5 w-3.5" aria-hidden="true" />
			) : tone === 'running' ? (
				<Loader2 className="text-primary h-3.5 w-3.5 animate-spin" aria-hidden="true" />
			) : (
				<span
					aria-hidden="true"
					className={cn(
						'h-2 w-2 rounded-full',
						tone === 'ok' ? 'bg-success' : 'bg-muted-foreground/40',
					)}
				/>
			)}
			{label ? <span className="sr-only">{label}</span> : null}
		</span>
	);
}
