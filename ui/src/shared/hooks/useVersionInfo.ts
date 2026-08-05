import { useQuery } from '@tanstack/react-query';
import { SystemService, type VersionResponse } from '@/shared/api';

/** Stable key for the running/latest version probe (`GET /system/version`).
 * Local (not in `sharedQueryKeys`): nothing cross-module invalidates it, and the
 * shell's banner + UserMenu version line share this one cache slice. */
export const versionInfoKey = ['system', 'version'] as const;

/**
 * The running app version and the latest release known to this backend.
 *
 * Powers the shell's "update available" banner and the current-version line in
 * the user menu. Polls on a modest interval so the banner appears without a
 * reload once the CLI reports a newer release. Failures resolve to a safe
 * "current unknown, no update" so a transient/missing endpoint (e.g. an older
 * backend that lacks `/system/version`, which fails fast as a non-retried 404)
 * never paints a misleading banner.
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
