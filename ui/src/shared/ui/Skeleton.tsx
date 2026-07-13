import { cn } from '@/shared/lib/utils';

/**
 * Placeholder block shown while content loads. Uses a shimmer sweep
 * (`.skeleton-shimmer`) rather than a flat pulse so loading reads clearly;
 * falls back to a calm opacity pulse under reduced-motion (the global media
 * reset neutralises the sweep animation).
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
	return (
		<div
			className={cn('bg-muted skeleton-shimmer rounded-md', className)}
			aria-hidden="true"
			{...props}
		/>
	);
}

/**
 * A list of placeholder rows — the canonical loading state for the dashboard
 * cards and access-request lists. Each row mimics a title + meta line with a
 * trailing affordance, so the skeleton has the same shape as the real content
 * (no layout jump when data lands). Announced once to assistive tech.
 */
export function SkeletonRows({ rows = 3, className }: { rows?: number; className?: string }) {
	return (
		<div
			role="status"
			aria-live="polite"
			aria-busy="true"
			className={cn('divide-border/60 divide-y', className)}
		>
			<span className="sr-only">Loading…</span>
			{Array.from({ length: rows }).map((_, i) => (
				<div key={i} className="flex items-center justify-between gap-3 py-2.5">
					<div className="min-w-0 flex-1 space-y-2">
						<Skeleton className="h-3.5" style={{ width: `${55 + ((i * 13) % 30)}%` }} />
						<Skeleton className="h-2.5 w-2/5" />
					</div>
					<Skeleton className="h-3 w-12 shrink-0" />
				</div>
			))}
		</div>
	);
}
