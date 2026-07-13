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
	type PermissionRule,
	type PermissionRuleEffect,
} from '@/shared/lib/accessRequests';

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
