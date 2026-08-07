/**
 * Webhooks service tier — TanStack Query hooks.
 *
 * The only data-access path for webhooks views: components call these hooks,
 * which call the repository (`./client`). Mutations invalidate the affected
 * slices and toast on settle, mirroring the agents/toolkits modules.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/ui';
import {
	createEndpoint,
	deleteEndpoint,
	getEndpoint,
	listDeliveries,
	listEndpoints,
	listEventTypes,
	pauseEndpoint,
	redeliverDelivery,
	resumeEndpoint,
	rotateEndpointSecret,
	sendTestEvent,
	updateEndpoint,
} from '@/modules/webhooks/api/client';
import type {
	EndpointCreateInput,
	EndpointCreateResult,
	EndpointPatch,
	EventTypeDef,
	RotateSecretResult,
	WebhookDeliveryEntity,
	WebhookEndpointEntity,
} from '@/modules/webhooks/api/types';

const webhooksKeys = {
	all: ['webhooks'] as const,
	list: () => [...webhooksKeys.all, 'list'] as const,
	detail: (id: string) => [...webhooksKeys.all, 'detail', id] as const,
	deliveries: (id: string, status: string, eventType: string) =>
		[...webhooksKeys.all, 'deliveries', id, status, eventType] as const,
	deliveriesRoot: (id: string) => [...webhooksKeys.all, 'deliveries', id] as const,
	eventTypes: ['webhooks', 'event-types'] as const,
};

function notifyError(error: unknown, fallback: string): void {
	toast({
		title: fallback,
		description: error instanceof Error ? error.message : undefined,
		variant: 'error',
	});
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

export function useWebhookEndpoints() {
	return useQuery<WebhookEndpointEntity[]>({
		queryKey: webhooksKeys.list(),
		queryFn: () => listEndpoints(),
	});
}

export function useWebhookEndpoint(id: string | null) {
	return useQuery<WebhookEndpointEntity>({
		queryKey: webhooksKeys.detail(id ?? ''),
		queryFn: () => getEndpoint(id as string),
		enabled: id != null,
	});
}

/** The subscribable event catalog. Small + slow-changing → cached generously. */
export function useEventTypeCatalog() {
	return useQuery<EventTypeDef[]>({
		queryKey: webhooksKeys.eventTypes,
		queryFn: () => listEventTypes(),
		staleTime: 5 * 60 * 1000,
	});
}

export function useWebhookDeliveries(
	id: string | null,
	filters: { status?: string | null; eventType?: string | null } = {},
) {
	const status = filters.status ?? 'all';
	const eventType = filters.eventType ?? 'all';
	return useQuery<WebhookDeliveryEntity[]>({
		queryKey: webhooksKeys.deliveries(id ?? '', status, eventType),
		queryFn: () =>
			listDeliveries(id as string, {
				status: status === 'all' ? null : status,
				eventType: eventType === 'all' ? null : eventType,
			}),
		enabled: id != null,
	});
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export function useCreateEndpoint() {
	const qc = useQueryClient();
	return useMutation<EndpointCreateResult, Error, EndpointCreateInput>({
		mutationFn: (input) => createEndpoint(input),
		onSuccess: (result) => {
			qc.setQueryData(webhooksKeys.detail(result.endpoint.id), result.endpoint);
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			toast({
				title: 'Endpoint created',
				description: `${result.endpoint.name} is now receiving events.`,
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to create the endpoint.'),
	});
}

export function useUpdateEndpoint() {
	const qc = useQueryClient();
	return useMutation<WebhookEndpointEntity, Error, { id: string; patch: EndpointPatch }>({
		mutationFn: ({ id, patch }) => updateEndpoint(id, patch),
		onSuccess: (endpoint) => {
			qc.setQueryData(webhooksKeys.detail(endpoint.id), endpoint);
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			toast({ title: 'Endpoint updated', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to update the endpoint.'),
	});
}

export function usePauseEndpoint() {
	const qc = useQueryClient();
	return useMutation<WebhookEndpointEntity, Error, string>({
		mutationFn: (id) => pauseEndpoint(id),
		onSuccess: (endpoint) => {
			qc.setQueryData(webhooksKeys.detail(endpoint.id), endpoint);
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			toast({
				title: 'Deliveries paused',
				description: 'Events fired while paused are not queued for later.',
				variant: 'success',
			});
		},
		onError: (e) => notifyError(e, 'Failed to pause the endpoint.'),
	});
}

export function useResumeEndpoint() {
	const qc = useQueryClient();
	return useMutation<WebhookEndpointEntity, Error, string>({
		mutationFn: (id) => resumeEndpoint(id),
		onSuccess: (endpoint) => {
			qc.setQueryData(webhooksKeys.detail(endpoint.id), endpoint);
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			toast({ title: 'Deliveries resumed', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to resume the endpoint.'),
	});
}

export function useRotateEndpointSecret() {
	const qc = useQueryClient();
	return useMutation<RotateSecretResult, Error, string>({
		mutationFn: (id) => rotateEndpointSecret(id),
		onSuccess: (_result, id) => {
			qc.invalidateQueries({ queryKey: webhooksKeys.detail(id) });
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
		},
		onError: (e) => notifyError(e, 'Failed to rotate the signing secret.'),
	});
}

export function useDeleteEndpoint() {
	const qc = useQueryClient();
	return useMutation<void, Error, string>({
		mutationFn: (id) => deleteEndpoint(id),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			toast({ title: 'Endpoint deleted', variant: 'success' });
		},
		onError: (e) => notifyError(e, 'Failed to delete the endpoint.'),
	});
}

export function useSendTestEvent(endpointId: string | null) {
	const qc = useQueryClient();
	return useMutation<WebhookDeliveryEntity, Error, string>({
		mutationFn: (eventType) => {
			if (!endpointId) {
				return Promise.reject(
					new Error('Cannot send a test event before the endpoint loads.'),
				);
			}
			return sendTestEvent(endpointId, eventType);
		},
		onSuccess: (delivery) => {
			if (endpointId) {
				qc.invalidateQueries({ queryKey: webhooksKeys.deliveriesRoot(endpointId) });
				qc.invalidateQueries({ queryKey: webhooksKeys.detail(endpointId) });
				qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			}
			toast({
				title:
					delivery.status === 'delivered' ? 'Test event delivered' : 'Test event failed',
				description:
					delivery.status === 'delivered'
						? `Endpoint answered ${delivery.lastHttpStatus ?? '—'} in ${delivery.lastLatencyMs ?? '—'} ms.`
						: (delivery.attempts[delivery.attempts.length - 1]?.error ??
							`Endpoint answered ${delivery.lastHttpStatus ?? 'nothing'}.`),
				variant: delivery.status === 'delivered' ? 'success' : 'error',
			});
		},
		onError: (e) => notifyError(e, 'Failed to send the test event.'),
	});
}

export function useRedeliverDelivery(endpointId: string | null) {
	const qc = useQueryClient();
	return useMutation<WebhookDeliveryEntity, Error, string>({
		mutationFn: (deliveryId) => {
			if (!endpointId) {
				return Promise.reject(new Error('Cannot redeliver before the endpoint loads.'));
			}
			return redeliverDelivery(endpointId, deliveryId);
		},
		onSuccess: (delivery) => {
			if (endpointId) {
				qc.invalidateQueries({ queryKey: webhooksKeys.deliveriesRoot(endpointId) });
				qc.invalidateQueries({ queryKey: webhooksKeys.detail(endpointId) });
				qc.invalidateQueries({ queryKey: webhooksKeys.list() });
			}
			toast({
				title:
					delivery.status === 'delivered'
						? 'Event redelivered'
						: 'Redelivery attempt failed',
				variant: delivery.status === 'delivered' ? 'success' : 'error',
			});
		},
		onError: (e) => notifyError(e, 'Failed to redeliver the event.'),
	});
}
