import { useQuery } from '@tanstack/react-query';
import { SystemService, type VersionResponse } from '@/shared/api';

/** Stable key for the running/latest version probe (`GET /system/version`).
 * Local (not in `sharedQueryKeys`): nothing cross-module invalidates it, and the
 * shell's banner + UserMenu version line share this one cache slice. */
export const versionInfoKey = ['system', 'version'] as const;

/**
 * The running app version and the latest available release.
 *
 * Powers the shell's "update available" banner and the current-version line in
 * the user menu. Both render only inside the authenticated shell, so the request
 * always carries the session. The backend resolves the latest release itself
 * (cached, best-effort). Polls on a modest interval so the banner appears without
 * a reload once a newer release is published. Failures resolve to a safe "current
 * unknown, no update" so a transient error or an older backend that lacks
 * `/system/version` (a non-retried 404) never paints a misleading banner.
 */
export function useVersionInfo(): VersionResponse {
	const { data } = useQuery({
		queryKey: versionInfoKey,
		queryFn: () => SystemService.getVersion(),
		staleTime: 300_000,
		refetchInterval: 300_000,
	});
	return {
		current: data?.current ?? '',
		latest: data?.latest ?? null,
		update_available: data?.update_available ?? false,
	};
}
