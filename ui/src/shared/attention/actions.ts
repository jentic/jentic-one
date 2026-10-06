/**
 * The inline verb an attention item can be cleared with — approve a waiting
 * agent. Shared (not per-module) because two surfaces offer it: the top-bar
 * Notifications menu (shell) and the Home inbox.
 *
 * It invalidates every surface that counts the item, so the badge, the inbox
 * and the Activity rail drop the row together.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AgentsService, sharedQueryKeys, type AgentResponse } from '@/shared/api';

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
