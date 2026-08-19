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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	createEndpoint,
	deleteEndpoint,
	listDeliveries,
	listEndpoints,
	resendDelivery,
	rotateSecret,
	sendTestEvent,
	type CreateEndpointParams,
} from '@/modules/webhooks/api/client';
import type {
	CreatedEndpoint,
	RotatedSecret,
	WebhookDeliveryEntity,
	WebhookEndpointEntity,
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
 * The delivery log for one endpoint.
 *
 * Polls while the panel is open because deliveries are processed by a background
 * dispatcher, not by the request that queued them: without refetching, a freshly
 * queued row would sit at `pending` on screen long after it had actually been
 * sent. 5s is comfortably inside the dispatcher's tick.
 */
export function useWebhookDeliveries(endpointId: string | null) {
	return useQuery<WebhookDeliveryEntity[]>({
		queryKey: webhookKeys.deliveries(endpointId ?? ''),
		queryFn: () => listDeliveries(endpointId as string),
		enabled: Boolean(endpointId),
		refetchInterval: endpointId ? 5000 : false,
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
		onSuccess: (_void, { endpointId }) => {
			qc.invalidateQueries({ queryKey: webhookKeys.deliveries(endpointId) });
			toast({ title: 'Delivery requeued', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to resend the delivery.'),
	});
}
