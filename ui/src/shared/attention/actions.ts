/**
 * The inline verbs an attention item can be cleared with — approve a waiting
 * agent, acknowledge an alert. Shared (not per-module) because two surfaces
 * offer them: the top-bar Notifications menu (shell) and the Home inbox.
 *
 * Both invalidate every surface that counts the item, so the badge, the inbox
 * and the Activity rail drop the row together.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
	AgentsService,
	EventsService,
	sharedQueryKeys,
	type AgentResponse,
	type EventResponse,
} from '@/shared/api';
import { attentionKeys } from '@/shared/attention/useAttentionItems';
import { useAgentStreamOptional } from '@/shared/lib/agentStream';

export function useApproveAgent() {
	const queryClient = useQueryClient();
	return useMutation<AgentResponse, Error, string>({
		mutationFn: (agentId) => AgentsService.approveAgent({ agentId }),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.agentsRoot });
			void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.attentionRoot });
		},
	});
}

export function useAcknowledgeAttention() {
	const queryClient = useQueryClient();
	const stream = useAgentStreamOptional();
	return useMutation<EventResponse, Error, string>({
		mutationFn: (eventId) =>
			EventsService.acknowledgeEvent({ eventId, requestBody: { acknowledged: true } }),
		onSuccess: (_data, eventId) => {
			// The live stream never re-delivers an ack flip; sync its copy by hand.
			stream?.resolveEvent(eventId);
			void queryClient.invalidateQueries({ queryKey: attentionKeys.events });
			void queryClient.invalidateQueries({ queryKey: sharedQueryKeys.monitorEventsRoot });
		},
	});
}
