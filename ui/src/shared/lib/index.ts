/**
 * Barrel for cross-cutting `shared/lib` repositories that feature modules may
 * consume through a single bare import (`@/shared/lib`). The ESLint layering
 * rule forbids deep `@/shared/lib/*` imports from `src/modules/**`, so anything
 * a module needs is surfaced here.
 *
 * Kept intentionally narrow — only the access-request repository (the durable
 * approval queue the Dashboard's "Pending requests" card reads) is exposed.
 * Do NOT re-export the rail's React providers/components here; those are app
 * shell concerns, not module-consumable repositories.
 */
export {
	listAccessRequests,
	getAccessRequest,
	decideAccessRequest,
	decideAllPending,
	amendAccessRequest,
	itemTargetLabel,
	isSpecificResource,
	isScopeGrant,
	scopeLabel,
	rulesAreEnforceable,
	parseItemRules,
	ruleSummary,
	isUnrestrictedAllow,
	type AccessRequest,
	type AccessRequestItem,
	type AccessRequestEvaluation,
	type AccessRequestEvaluationCheck,
	type AccessRequestPage,
	type ListAccessRequestsParams,
	type ItemDecision,
	type ItemAmendment,
	type PermissionRule,
	type PermissionRuleEffect,
} from '@/shared/lib/accessRequests';

// Provisioning-plan classification/shape helpers — used by the fulfilment
// wizard that decides `--provision` requests (create → amend → approve).
export {
	isProvisioningPlan,
	planApiReference,
	planAuthType,
	planIsNoAuth,
	planSteps,
	findItem,
	itemKey,
	FULFILMENT_ITEM_TYPES,
	type PlanApiReference,
	type PlanStep,
} from '@/shared/lib/provisioningPlan';

// Source-agnostic scope primitives — shared by the credentials OAuth2 scope
// picker and the actor (agent/service-account) platform-permission picker.
export {
	type ScopeOrigin,
	type EnhancedScope,
	type ScopeGroup,
	extractResourceFromScope,
	formatResourceName,
	groupScopesByResource,
	scopesInGroup,
	filterScopeGroups,
} from '@/shared/lib/scopes';

export { fetchActorDirectory } from '@/shared/lib/actorDirectory';

// Canonical per-severity event icon — the single source of truth shared by
// Monitor's Events tab and the Dashboard's "Needs attention" card so the same
// event reads identically in both surfaces.
export { eventSeverityIcon } from '@/shared/lib/eventSeverity';

// API-identity display helpers — one humanising rule applied everywhere a
// machine identity (`api_id` / `api_vendor` / `api_name`) needs to render as a
// friendly primary line. Originally lived in `modules/discover/api/adapters.ts`
// (`titleFromApiId`); moved here so Discover, the credential picker, and the
// toolkit surfaces all share the same rule.
export {
	humanizeDomainSlug,
	humanizeName,
	titleFromApiId,
	toolkitCredDisplayName,
	apiRefDisplayName,
} from '@/shared/lib/api-display';

// Shared Edit-dialog validators — one copy of the name/description constraints
// (non-empty name ≤255, optional description ≤1024) used by both the agent and
// toolkit rename dialogs so their caps + error copy can't drift.
export {
	validateName,
	validateDescription,
	NAME_MAX_LENGTH,
	DESCRIPTION_MAX_LENGTH,
} from '@/shared/lib/validators';
