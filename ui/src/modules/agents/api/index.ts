/**
 * Agents module public API surface.
 *
 * Components/pages import from here only — never from `./client` or
 * `@/shared/api` directly (ESLint-enforced layering).
 */
export {
	useAgents,
	useAgent,
	useAgentToolkits,
	useAgentApiKeyInfo,
	useAgentApiKeyHistory,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useArchiveAgent,
	useCreateAgent,
	useGenerateAgentApiKey,
	useRevokeAgentApiKey,
	useGenerateServiceAccountApiKey,
	useServiceAccounts,
	useServiceAccount,
	useCreateServiceAccount,
	useApproveServiceAccount,
	useDenyServiceAccount,
	useDisableServiceAccount,
	useEnableServiceAccount,
	useArchiveServiceAccount,
	usePermissionCatalogue,
	useAgentScopes,
	useReplaceAgentScopes,
	useServiceAccountScopes,
	useReplaceServiceAccountScopes,
	useActorAccessRequests,
	actorAccessRequestsKey,
	actorAccessRequestsRootKey,
} from '@/modules/agents/api/hooks';

export { AgentsApiError } from '@/modules/agents/api/client';

export {
	ACTOR_STATUSES,
	STATUS_LABELS,
	STATUS_BADGE_VARIANT,
	STATUS_DOT,
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
	toActorStatus,
} from '@/modules/agents/api/types';

export type {
	ActorStatus,
	ActorVerb,
	AgentAction,
	AgentEntity,
	ApiKeyHistoryEntry,
	ApiKeyInfoEntity,
	ApiKeyResult,
	PermissionCatalogEntry,
	ServiceAccountEntity,
	ToolkitBindingEntity,
	Attribution,
} from '@/modules/agents/api/types';

export type { AccessRequest } from '@/shared/lib';
