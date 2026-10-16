import type { QueryClient } from '@tanstack/react-query';
import { createQueryClient } from '@/shared/app';

/**
 * The app's QueryClient: its retry policy as shipped, with no backoff.
 *
 * Every retry the policy allows fires at once, so a test waits on the settled
 * UI rather than on wall-clock time. A query only exposes its error once its
 * retries are spent, so a read count taken when the error state renders is
 * final.
 */
export function appQueryClient(): QueryClient {
	const client = createQueryClient();
	const defaults = client.getDefaultOptions();
	client.setDefaultOptions({ ...defaults, queries: { ...defaults.queries, retryDelay: 0 } });
	return client;
}
