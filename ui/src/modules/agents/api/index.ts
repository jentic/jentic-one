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
	useAgentBindingRuleSummaries,
	useRetryBindingRules,
	summarizeBindingRules,
	useBindAgentCredential,
	useUnbindAgentCredential,
	usePurgeOrphanBindings,
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
	usePermissionCatalogue,
	useAgentScopes,
	useReplaceAgentScopes,
	useAgentOauthGrants,
	useRevokeOauthGrant,
	useActorUsageDetail,
	useCredentialUsageTotals,
	useActorExecutions,
	useActorAudit,
	useUpdateAgent,
	useMcpSessions,
	useLatestMcpActivity,
	useInstanceIdentity,
	ServingRefreshError,
} from '@/modules/agents/api/hooks';
export type { BindingRuleSummary, BindingRulesState } from '@/modules/agents/api/hooks';

export { AgentsApiError } from '@/modules/agents/api/client';
export type { ActorUsageDetail, AgentPatch } from '@/modules/agents/api/client';

export {
	STATUS_DOT,
	ACTIONS_FOR_STATUS,
	ACTION_LABEL,
	ACTION_VARIANT,
} from '@/modules/agents/api/types';

export type {
	ActorStatus,
	AgentAction,
	AgentEntity,
	BindingPermissionRule,
	BindingPermissionTestResult,
	CredentialBindingEntity,
	McpSessionEntity,
	OAuthGrantEntity,
	PermissionCatalogEntry,
	PermissionRuleInput,
} from '@/modules/agents/api/types';

export { mcpClientLabel } from '@/modules/agents/api/types';
