/**
 * DiscoverStatusRow — whole-manifest counts + freshness for the Discover header.
 *
 * Reads `catalog_total` / `registered_count` / `manifest_age_seconds` off the
 * catalog response. These describe the WHOLE manifest (not the current page or
 * filtered set) and stay constant while paging, so the row doesn't flicker as
 * the user scrolls. `manifest_age_seconds === null` means the catalog has never
 * been fetched / has no snapshot yet.
 */
import { Database } from 'lucide-react';
import { Skeleton } from '@/shared/ui';

interface DiscoverStatusRowProps {
	catalogTotal: number;
	registeredCount: number;
	manifestAgeSeconds: number | null;
	loading: boolean;
}

function formatAge(seconds: number | null): string {
	if (seconds === null) return 'never refreshed';
	if (seconds < 60) return 'updated just now';
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `updated ${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `updated ${hours}h ago`;
	const days = Math.floor(hours / 24);
	return `updated ${days}d ago`;
}

export function DiscoverStatusRow({
	catalogTotal,
	registeredCount,
	manifestAgeSeconds,
	loading,
}: DiscoverStatusRowProps) {
	if (loading) {
		return <Skeleton className="h-4 w-64" data-testid="discover-status-loading" />;
	}

	return (
		<p
			className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-sm"
			data-testid="discover-status"
		>
			<Database size={14} aria-hidden="true" />
			<span>
				<strong className="text-foreground font-medium">
					{catalogTotal.toLocaleString()}
				</strong>{' '}
				APIs in the catalog
			</span>
			<span aria-hidden="true">·</span>
			<span>
				<strong className="text-foreground font-medium">
					{registeredCount.toLocaleString()}
				</strong>{' '}
				imported
			</span>
			<span aria-hidden="true">·</span>
			<span>{formatAge(manifestAgeSeconds)}</span>
		</p>
	);
}
