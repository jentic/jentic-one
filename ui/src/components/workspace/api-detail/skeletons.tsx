import { Skeleton } from '@/components/ui/Skeleton';

/**
 * Full-page skeleton matching the section layout of `ApiDetailView`:
 * overview strip, credentials, toolkits, operations, workflows. Stays
 * close to the real DOM so the layout doesn't visibly shift on hydrate.
 */
export function ApiDetailSkeleton() {
	return (
		<div className="space-y-8">
			<section className="border-border/50 rounded-lg border">
				<div className="border-border/30 border-b px-4 py-3">
					<Skeleton className="h-3 w-16" />
					<Skeleton className="mt-1.5 h-4 w-56" />
				</div>
				<div className="flex flex-wrap gap-x-6 gap-y-2 px-4 py-3">
					<Skeleton className="h-4 w-20" />
					<Skeleton className="h-4 w-16" />
					<Skeleton className="h-4 w-20" />
					<Skeleton className="h-4 w-20" />
				</div>
			</section>

			<section className="space-y-3">
				<Skeleton className="h-5 w-28" />
				<Skeleton className="h-14 w-full rounded-lg" />
				<Skeleton className="h-14 w-full rounded-lg" />
			</section>

			<section className="space-y-3">
				<Skeleton className="h-5 w-20" />
				<Skeleton className="h-14 w-full rounded-lg" />
			</section>

			<section className="space-y-3">
				<Skeleton className="h-5 w-24" />
				<Skeleton className="h-9 w-64 rounded-lg" />
				<div className="space-y-1">
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
					<Skeleton className="h-11 w-full rounded-lg" />
				</div>
			</section>

			<section className="space-y-3">
				<Skeleton className="h-5 w-24" />
				<Skeleton className="h-12 w-full rounded-lg" />
				<Skeleton className="h-12 w-full rounded-lg" />
			</section>
		</div>
	);
}

/**
 * Inline skeleton used while the first page of operations is still
 * loading. Renders the toolbar plus a stack of placeholder rows.
 */
export function OperationsSkeleton() {
	return (
		<div className="mt-3 space-y-2">
			<Skeleton className="h-8 w-64 rounded-lg" />
			{Array.from({ length: 5 }).map((_, i) => (
				<Skeleton key={i} className="h-12 w-full rounded-lg" />
			))}
		</div>
	);
}
