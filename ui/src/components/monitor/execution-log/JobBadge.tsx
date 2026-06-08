import { type JSX, type MouseEvent } from 'react';
import { Briefcase } from 'lucide-react';

/**
 * Inline cross-link badge rendered next to an execution that originated from
 * an async job. Clicking it navigates the parent surface to the Jobs tab and
 * (eventually) opens the matching job — for now just delegates to the
 * `onClick` handler the parent passes.
 *
 * Visual is intentionally low-noise (icon + monospaced suffix) because the
 * Execution Log row is already dense; the badge needs to communicate "this
 * also has a job side" without drawing the eye away from status / API.
 *
 * The badge stops event propagation so clicking it does not also trigger
 * the row's "open detail" handler — these are two different navigations.
 */
export interface JobBadgeProps {
	jobId: string;
	onOpen?: (jobId: string) => void;
}

export function JobBadge({ jobId, onOpen }: JobBadgeProps): JSX.Element {
	const suffix = jobId.startsWith('job_') ? jobId.slice(4) : jobId;
	const display = suffix.length > 6 ? suffix.slice(0, 6) : suffix;

	function handleClick(event: MouseEvent<HTMLButtonElement>): void {
		event.stopPropagation();
		onOpen?.(jobId);
	}

	return (
		<button
			type="button"
			onClick={handleClick}
			title={`Open job ${jobId}`}
			aria-label={`Open job ${jobId}`}
			className="border-border bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] leading-none transition-colors"
		>
			<Briefcase className="h-3 w-3" aria-hidden="true" />
			<span>job·{display}</span>
		</button>
	);
}
