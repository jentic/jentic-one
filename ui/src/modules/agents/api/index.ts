/**
 * Agents module public API surface.
 *
 * Components/pages import from here only — never from `./client` or
 * `@/shared/api` directly (ESLint-enforced layering).
 */
export {
	useAgents,
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
	usePrimeBindingPermissions,
	useAgentBindingPermissions,
	useAgentBindingEffectiveRules,
	useDetachAgentBindingRuleSet,
	useReplaceAgentBindingPermissions,
	useTestAgentBindingPermissions,
	useAgentApiKeyInfo,
	useAgentApiKeyHistory,
	useApproveAgent,
	useDenyAgent,
	useDisableAgent,
	useSetAgentServing,
	useArchiveAgent,
	useCreateAgent,
	useGenerateAgentApiKey,
	useIsGeneratingAgentApiKey,
	useRevokeAgentApiKey,
	usePermissionCatalogue,
	useAgentPermissions,
	useReplaceAgentPermissions,
	useAgentOauthGrants,
	useRevokeOauthGrant,
	useActorUsageDetail,
	useCredentialUsageTotals,
	useActorExecutions,
	useActorApiUsage,
	useActorRecentCalls,
	useActorAudit,
	useUpdateAgent,
	useMcpSessions,
	useLatestMcpActivity,
	useInstanceIdentity,
	ServingRefreshError,
} from '@/modules/agents/api/hooks';
export type {
	BindingRuleSummary,
	BindingRulesState,
	AgentBindingEffectiveRules,
} from '@/modules/agents/api/hooks';

export {
	AgentsApiError,
	isAgentsAccessDenied,
	isAgentsSessionEnded,
} from '@/modules/agents/api/client';
export type {
	ActorApiUsage,
	ActorExecutionEntity,
	ActorUsageDetail,
	AgentPatch,
	ApiCredentialUsage,
} from '@/modules/agents/api/client';

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
	BindingRuleSetEntity,
	CredentialBindingEntity,
	McpSessionEntity,
	OAuthGrantEntity,
	PermissionCatalogEntry,
	PermissionRuleInput,
} from '@/modules/agents/api/types';

export { mcpClientLabel } from '@/modules/agents/api/types';
