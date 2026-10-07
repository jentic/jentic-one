/**
 * DiscoverStatusRow — whole-manifest counts + freshness for the Library header,
 * as one `CountLine`: "6,345 APIs in the catalog from 1,200+ vendors so far · 3
 * in your workspace · 1 update available · updated 2m ago".
 *
 * Reads `catalog_total` / `registered_count` / `outdated_count` /
 * `manifest_age_seconds` off the catalog response. These describe the WHOLE
 * manifest (not the current page or filtered set) and stay constant while
 * paging, so the row doesn't flicker as the user scrolls.
 * `manifest_age_seconds === null` means the catalog has never been fetched / has
 * no snapshot yet. The "N update(s) available" segment renders only when
 * `outdated_count > 0`. The catalog response carries no vendor total, so the
 * vendor figure counts the vendors LOADED so far — a floor (`N+ … so far`)
 * until the whole catalog has paged in, and omitted when nothing is loaded.
 */
import { ArrowUpCircle } from 'lucide-react';
import { CountLine, Skeleton } from '@/shared/ui';

interface DiscoverStatusRowProps {
	catalogTotal: number;
	registeredCount: number;
	outdatedCount: number;
	manifestAgeSeconds: number | null;
	loading: boolean;
	/** Distinct vendors among the loaded catalog rows (0 = omit the figure). */
	vendorsLoaded?: number;
	/** Every catalog page is loaded, so `vendorsLoaded` is the whole count. */
	vendorsComplete?: boolean;
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
	outdatedCount,
	manifestAgeSeconds,
	loading,
	vendorsLoaded = 0,
	vendorsComplete = false,
}: DiscoverStatusRowProps) {
	if (loading) {
		return <Skeleton className="h-8 w-96 max-w-full" data-testid="discover-status-loading" />;
	}
	const vendorText = vendorsComplete
		? `${vendorsLoaded.toLocaleString()} vendor${vendorsLoaded === 1 ? '' : 's'}`
		: `${vendorsLoaded.toLocaleString()}+ vendors`;

	return (
		<CountLine
			data-testid="discover-status"
			value={catalogTotal.toLocaleString()}
			label={
				<>
					APIs in the catalog
					{vendorsLoaded > 0 && (
						<>
							{' '}
							from <b data-testid="discover-status-vendors">{vendorText}</b>
							{!vendorsComplete && ' so far'}
						</>
					)}
				</>
			}
			details={[
				<span key="ws" className="text-success" data-testid="discover-status-workspace">
					{registeredCount.toLocaleString()} in your workspace
				</span>,
				outdatedCount > 0 && (
					<span
						key="up"
						className="inline-flex items-center gap-1"
						data-testid="discover-status-outdated"
					>
						<ArrowUpCircle className="text-caution h-3.5 w-3.5" aria-hidden="true" />
						{outdatedCount.toLocaleString()} update{outdatedCount === 1 ? '' : 's'}{' '}
						available
					</span>
				),
				<span key="age">{formatAge(manifestAgeSeconds)}</span>,
			]}
		/>
	);
}
