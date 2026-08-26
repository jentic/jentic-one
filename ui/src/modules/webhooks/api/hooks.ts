/**
 * Webhooks service tier — TanStack Query hooks.
 *
 * The only backend access path for Webhooks views: components call these hooks,
 * which call the repository (`./client`), which calls `@/shared/api`. Views must
 * never reach past this layer (ESLint-enforced).
 *
 * Two deliberate departures from the module's usual shape, both about secrets:
 *
 * 1. **Create and rotate return their secret to the caller and never cache it.**
 *    The plaintext is unrecoverable after the response, so the component shows
 *    it once and drops it. Putting it in the query cache would keep it in memory
 *    (and in devtools) for the rest of the session.
 * 2. **Neither emits a success toast containing the secret.** A toast is
 *    transient and dismissible — precisely the wrong container for a value the
 *    user must copy exactly once.
 */
import {
	useMutation,
	useQueries,
	useQuery,
	useQueryClient,
	type Query,
} from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	createEndpoint,
	deleteEndpoint,
	getEndpoint,
	getEndpointStats,
	getEventCatalog,
	listDeliveries,
	listDeliveryAttempts,
	listEndpoints,
	resendDelivery,
	rotateSecret,
	sendTestEvent,
	updateEndpoint,
	type CreateEndpointParams,
	type UpdateEndpointParams,
} from '@/modules/webhooks/api/client';
import type {
	CreatedEndpoint,
	RotatedSecret,
	WebhookDeliveryAttemptEntity,
	WebhookDeliveryEntity,
	WebhookEndpointEntity,
	WebhookEndpointStats,
} from '@/modules/webhooks/api/types';

/**
 * Module-private query keys. Not in the cross-module `sharedQueryKeys` registry
 * because no sibling module reads or invalidates webhook state today; move a
 * root there if one ever does.
 */
export const webhookKeys = {
	all: ['webhooks'] as const,
	endpoints: () => [...webhookKeys.all, 'endpoints'] as const,
	endpoint: (id: string) => [...webhookKeys.all, 'endpoint', id] as const,
	deliveries: (endpointId: string) => [...webhookKeys.all, 'deliveries', endpointId] as const,
	stats: (endpointId: string) => [...webhookKeys.all, 'stats', endpointId] as const,
	attempts: (deliveryId: string) => [...webhookKeys.all, 'attempts', deliveryId] as const,
	catalog: () => [...webhookKeys.all, 'event-catalog'] as const,
};

function notifyError(error: unknown, fallback: string): void {
	toast({
		title: fallback,
		description: error instanceof Error ? error.message : undefined,
		variant: 'error',
	});
}

export function useWebhookEndpoints() {
	return useQuery<WebhookEndpointEntity[]>({
		queryKey: webhookKeys.endpoints(),
		queryFn: () => listEndpoints(),
	});
}

/**
 * A single endpoint by id — the detail page's own query (the drawer read the
 * open row out of the list; a routed page is directly addressable, including on
 * a hard refresh, so it fetches the endpoint itself). Refreshes on the same 5s
 * cadence the deliveries/stats slices use so the config, active flag, and the
 * rotation grace badge stay current after a mutation, but never in the
 * background.
 */
export function useWebhookEndpoint(endpointId: string | null) {
	return useQuery<WebhookEndpointEntity>({
		queryKey: webhookKeys.endpoint(endpointId ?? ''),
		queryFn: () => getEndpoint(endpointId as string),
		enabled: Boolean(endpointId),
		refetchIntervalInBackground: false,
		refetchInterval: endpointId ? 5000 : false,
	});
}

/**
 * The delivery log for one endpoint.
 *
 * Deliveries are processed by a background dispatcher, not by the request that
 * queued them: without refetching, a freshly queued row would sit at `pending`
 * on screen long after it had actually been sent. So the query polls — but only
 * while it needs to:
 *
 * - **Stops when everything is terminal.** Once every row is `succeeded` or
 *   `dead` nothing more will change on its own, so the interval turns off
 *   (`refetchInterval` returns `false`) and resumes only when a mutation
 *   (test/resend) puts a row back in flight and invalidates the slice.
 * - **Never polls in the background.** `refetchIntervalInBackground: false`
 *   (the default, stated here for intent) means a hidden tab or a backgrounded
 *   drawer doesn't burn requests; the poll picks back up on focus.
 *
 * 5s is comfortably inside the dispatcher's tick.
 */
const TERMINAL_STATUSES: ReadonlySet<WebhookDeliveryEntity['status']> = new Set([
	'succeeded',
	'dead',
]);

export function useWebhookDeliveries(endpointId: string | null) {
	return useQuery<WebhookDeliveryEntity[]>({
		queryKey: webhookKeys.deliveries(endpointId ?? ''),
		queryFn: () => listDeliveries(endpointId as string),
		enabled: Boolean(endpointId),
		refetchIntervalInBackground: false,
		refetchInterval: (query) => {
			if (!endpointId) return false;
			const rows = query.state.data;
			// No data yet, or something is still in flight → keep polling. Once
			// every row has reached a terminal state there is nothing left to
			// watch, so stop until a mutation reopens the slice.
			if (!rows || rows.length === 0) return 5000;
			const settled = rows.every((row) => TERMINAL_STATUSES.has(row.status));
			return settled ? false : 5000;
		},
	});
}

/**
 * Aggregate delivery health for one endpoint (the Overview KPI strip).
 * Refreshes on the same 5s cadence the delivery log uses, so a freshly queued
 * test's outcome shows up in the counts too, but never in the background.
 */
export function useWebhookEndpointStats(endpointId: string | null) {
	return useQuery<WebhookEndpointStats>({
		queryKey: webhookKeys.stats(endpointId ?? ''),
		queryFn: () => getEndpointStats(endpointId as string),
		enabled: Boolean(endpointId),
		refetchIntervalInBackground: false,
		refetchInterval: endpointId ? 5000 : false,
	});
}

/** Per-endpoint stats, keyed by endpoint id, as loaded by {@link useWebhookEndpointStatsList}. */
export interface WebhookEndpointStatsResult {
	/** The aggregate delivery health, once loaded. */
	data: WebhookEndpointStats | undefined;
	isLoading: boolean;
	/** True once this endpoint's stats query has settled (success or error). */
	isSettled: boolean;
	isError: boolean;
}

/**
 * How many endpoints the list fans out `/stats` for. A webhooks console is a
 * handful of endpoints, not thousands, but the fan-out is one polling query per
 * endpoint, so an unbounded list would mount N forever-polling queries. We cap
 * it at the first {@link STATS_FANOUT_CAP} endpoints (the ones a user scans
 * first); beyond the cap a row degrades to the neutral "Health unknown" pill —
 * the exact same graceful-degradation path as a not-yet-loaded stat — rather
 * than blocking the whole surface on a request storm. 20 comfortably covers a
 * real console; if lists ever grow past it the right fix is server-side list
 * pagination feeding this hook a page at a time.
 */
export const STATS_FANOUT_CAP = 20;

/**
 * True once an endpoint's delivery health has nothing left to watch: no pending
 * (queued) and no failed (retrying) deliveries. Those are the only two states
 * that advance on their own via the background dispatcher, so once both are
 * zero the counts are stable until a mutation reopens the slice — mirroring the
 * terminal-stop {@link useWebhookDeliveries} uses on the per-row status.
 */
function statsSettled(stats: WebhookEndpointStats | undefined): boolean {
	if (!stats) return false;
	const pending = stats.countsByStatus.pending ?? 0;
	const retrying = stats.countsByStatus.failed ?? 0;
	return pending === 0 && retrying === 0;
}

/**
 * Delivery health for the endpoints on the list, at a glance.
 *
 * There is no aggregate/summary endpoint — health is only exposed per endpoint
 * via `/stats` (the same source the detail Overview reads). So the list fans out
 * one `/stats` query per endpoint with {@link useQueries}, reusing the exact
 * `webhookKeys.stats(id)` keys the detail page already uses: the cache is
 * shared, so opening an endpoint's detail (or coming back from it) is warm
 * rather than a fresh fetch. Each query degrades independently, so one failing
 * endpoint never blanks the strip or the list.
 *
 * The fan-out is **bounded** on two axes so it can't become a request storm:
 *
 * - **Capped** at the first {@link STATS_FANOUT_CAP} ids (see there). Ids past
 *   the cap simply aren't fetched; their rows fall through to the "unknown"
 *   pill via the same missing-data path as a slow query.
 * - **Self-stopping.** Each query polls at 5s only while it has something in
 *   flight (a pending or retrying delivery); once an endpoint's counts settle
 *   the interval turns off (`refetchInterval` returns `false`) and resumes when
 *   a mutation invalidates the slice — the same pattern as the delivery log.
 *   Combined with `refetchIntervalInBackground: false`, a healthy, backgrounded
 *   list makes no requests at all.
 *
 * Returns a `Map` keyed by endpoint id plus rolled-up loading/aggregate state
 * for the overview strip; a missing entry (or `data: undefined`) means that
 * endpoint's health is not yet known and callers should degrade gracefully.
 */
export function useWebhookEndpointStatsList(endpointIds: string[]) {
	const fetchedIds = endpointIds.slice(0, STATS_FANOUT_CAP);
	const results = useQueries({
		queries: fetchedIds.map((id) => ({
			queryKey: webhookKeys.stats(id),
			queryFn: () => getEndpointStats(id),
			refetchIntervalInBackground: false,
			// Stop once this endpoint's counts are stable; resume on invalidation.
			refetchInterval: (query: Query<WebhookEndpointStats>) =>
				statsSettled(query.state.data) ? (false as const) : 5000,
		})),
	});

	const byId = new Map<string, WebhookEndpointStatsResult>();
	fetchedIds.forEach((id, i) => {
		const r = results[i];
		byId.set(id, {
			data: r?.data,
			isLoading: r?.isLoading ?? false,
			isSettled: Boolean(r) && !r.isLoading,
			isError: r?.isError ?? false,
		});
	});

	// Loading only while nothing has settled yet — once even one endpoint's
	// stats land the strip can start showing partial (degraded) totals.
	const anySettled = results.some((r) => !r.isLoading);
	const allErrored = results.length > 0 && results.every((r) => r.isError);

	return {
		byId,
		/** True until at least one endpoint's stats have settled. */
		isLoading: results.length > 0 && !anySettled,
		/** Every stats query failed — the strip should degrade, not lie. */
		isError: allErrored,
	};
}

/** The per-attempt history for one delivery (newest first). Fetched on demand. */
export function useWebhookDeliveryAttempts(deliveryId: string | null) {
	return useQuery<WebhookDeliveryAttemptEntity[]>({
		queryKey: webhookKeys.attempts(deliveryId ?? ''),
		queryFn: () => listDeliveryAttempts(deliveryId as string),
		enabled: Boolean(deliveryId),
	});
}

/**
 * The backend's subscribable event catalog. The picker uses this as the source
 * of truth for *which* types exist (so it can never drift from the backend) and
 * the module's curated catalog for the human copy. Effectively static, so it is
 * cached hard and never refetched on focus.
 */
export function useWebhookEventCatalog() {
	return useQuery<string[]>({
		queryKey: webhookKeys.catalog(),
		queryFn: () => getEventCatalog(),
		staleTime: Infinity,
		gcTime: Infinity,
		refetchOnWindowFocus: false,
	});
}

/**
 * Create an endpoint. Resolves with the one-time secret so the caller can reveal
 * it; deliberately not written to the cache (see the module note above).
 */
export function useCreateWebhookEndpoint() {
	const qc = useQueryClient();
	return useMutation<CreatedEndpoint, unknown, CreateEndpointParams>({
		mutationFn: (input) => createEndpoint(input),
		onSuccess: (created) => {
			qc.invalidateQueries({ queryKey: webhookKeys.endpoints() });
			toast({
				title: 'Webhook endpoint created',
				description: `${created.endpoint.name} is active. Copy the signing secret now — it cannot be shown again.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to create the webhook endpoint.'),
	});
}

/**
 * Update an endpoint's configuration (name, target URL, event types, active).
 *
 * Unlike create/rotate this involves **no secret**, so it caches nothing
 * sensitive and shows an ordinary success toast. Invalidates both the list and
 * the single-endpoint slice so the edited row repaints wherever it is read.
 */
export function useUpdateWebhookEndpoint() {
	const qc = useQueryClient();
	return useMutation<
		WebhookEndpointEntity,
		unknown,
		{ endpointId: string; changes: UpdateEndpointParams }
	>({
		mutationFn: ({ endpointId, changes }) => updateEndpoint(endpointId, changes),
		onSuccess: (endpoint) => {
			qc.invalidateQueries({ queryKey: webhookKeys.endpoint(endpoint.id) });
			qc.invalidateQueries({ queryKey: webhookKeys.endpoints() });
			toast({
				title: 'Webhook endpoint updated',
				description: `Saved changes to ${endpoint.name}.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to update the webhook endpoint.'),
	});
}

export function useDeleteWebhookEndpoint() {
	const qc = useQueryClient();
	return useMutation<void, unknown, string>({
		mutationFn: (endpointId) => deleteEndpoint(endpointId),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: webhookKeys.endpoints() });
			toast({ title: 'Webhook endpoint deleted', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to delete the webhook endpoint.'),
	});
}

export function useRotateWebhookSecret() {
	const qc = useQueryClient();
	return useMutation<RotatedSecret, unknown, { endpointId: string; graceSeconds?: number }>({
		mutationFn: ({ endpointId, graceSeconds }) => rotateSecret(endpointId, graceSeconds),
		onSuccess: (rotated) => {
			// The endpoint row itself carries the rotation expiry, so refresh it.
			qc.invalidateQueries({ queryKey: webhookKeys.endpoint(rotated.endpointId) });
			qc.invalidateQueries({ queryKey: webhookKeys.endpoints() });
		},
		onError: (e) => notifyError(e, 'Failed to rotate the signing secret.'),
	});
}

export function useSendTestEvent() {
	const qc = useQueryClient();
	return useMutation<string, unknown, string>({
		mutationFn: (endpointId) => sendTestEvent(endpointId),
		onSuccess: (_deliveryId, endpointId) => {
			qc.invalidateQueries({ queryKey: webhookKeys.deliveries(endpointId) });
			qc.invalidateQueries({ queryKey: webhookKeys.stats(endpointId) });
			toast({
				title: 'Test event queued',
				description: 'The dispatcher will send it within a few seconds.',
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to queue a test event.'),
	});
}

/**
 * Requeue a delivery. Takes the endpoint id alongside the delivery id purely so
 * the right delivery-log slice can be invalidated — the API needs only the
 * delivery id.
 */
export function useResendDelivery() {
	const qc = useQueryClient();
	return useMutation<void, unknown, { deliveryId: string; endpointId: string }>({
		mutationFn: ({ deliveryId }) => resendDelivery(deliveryId),
		onSuccess: (_void, { deliveryId, endpointId }) => {
			qc.invalidateQueries({ queryKey: webhookKeys.deliveries(endpointId) });
			qc.invalidateQueries({ queryKey: webhookKeys.stats(endpointId) });
			qc.invalidateQueries({ queryKey: webhookKeys.attempts(deliveryId) });
			toast({ title: 'Delivery requeued', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to resend the delivery.'),
	});
}
