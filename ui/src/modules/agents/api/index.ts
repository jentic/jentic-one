/**
 * Agents module public API surface.
 *
 * Components/pages import from here only — never from `./client` or
 * `@/shared/api` directly (ESLint-enforced layering).
 */
export {
	useAgents,
	useAgent,
	usePendingAgents,
	useAgentCredentialBindings,
	useAgentsCredentialBindings,
	useRefreshFleetCredentialBindings,
	useAgentBindingRuleCounts,
	useAgentBindingRuleSummaries,
	useBindableCredentialsForAgent,
	useBindAgentCredential,
	useUnbindAgentCredential,
	useResumeAgentCredentialBinding,
	useInvalidateCredentialBindingSurfaces,
	useAgentBindingPermissions,
	useReplaceAgentBindingPermissions,
	useTestAgentBindingPermissions,
	useAgentApiKeyInfo,
	useAgentApiKeyHistory,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useEnableAgent,
	useSetAgentServing,
	useArchiveAgent,
	useCreateAgent,
	useGenerateAgentApiKey,
	useIsGeneratingAgentApiKey,
	useRevokeAgentApiKey,
	useGenerateServiceAccountApiKey,
	useServiceAccount,
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
	usePendingApproverAccessRequests,
	useAgentOauthGrants,
	useRevokeOauthGrant,
	useActorUsageDetail,
	useCredentialUsageTotals,
	useActorExecutions,
	useActorAudit,
	useUpdateAgent,
	useMcpSessions,
	useMcpLastSeen,
	useLatestMcpActivity,
	useInstanceIdentity,
	actorAccessRequestsKey,
	actorAccessRequestsRootKey,
	pendingApproverAccessRequestsKey,
	agentOauthGrantsKey,
	agentOauthGrantsRootKey,
	ServingRefreshError,
} from '@/modules/agents/api/hooks';
export type {
	SetServingVariables,
	PendingAgentsResult,
	BindingRuleSummary,
} from '@/modules/agents/api/hooks';

export { AgentsApiError } from '@/modules/agents/api/client';
export type {
	ActorAuditEntry,
	ActorUsageDetail,
	ActorExecutionEntity,
	UsageBucketEntity,
	AgentPatch,
} from '@/modules/agents/api/client';

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
	AgentBindableCredential,
	AgentEntity,
	ApiKeyHistoryEntry,
	ApiKeyInfoEntity,
	ApiKeyResult,
	BindingPermissionRule,
	BindingPermissionTestResult,
	CredentialBindingEntity,
	InstanceIdentityEntity,
	McpLastSeen,
	McpSessionEntity,
	OAuthGrantEntity,
	PermissionCatalogEntry,
	PermissionRuleInput,
	ServedApiEntity,
	ServiceAccountEntity,
	Attribution,
} from '@/modules/agents/api/types';

export { mcpClientLabel } from '@/modules/agents/api/types';

export type { AccessRequest } from '@/shared/lib';
