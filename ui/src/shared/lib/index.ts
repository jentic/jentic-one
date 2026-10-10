/**
 * Barrel for cross-cutting `shared/lib` repositories that feature modules may
 * consume through a single bare import (`@/shared/lib`). The ESLint layering
 * rule forbids deep `@/shared/lib/*` imports from `src/modules/**`, so anything
 * a module needs is surfaced here.
 *
 * Kept intentionally narrow. Do NOT re-export the rail's React
 * providers/components here; those are app shell concerns, not
 * module-consumable repositories.
 */

// Permission-rule display primitives — the typed broker-rule shape and the
// shared humanising summary used by the binding permissions editor/tester.
export {
	ruleSummary,
	type PermissionRule,
	type PermissionRuleEffect,
	type PermissionRuleMatchMode,
} from '@/shared/lib/permissionRules';

// Source-agnostic scope primitives — shared by the credentials OAuth2 scope
// picker and the agent platform-permission picker.
export {
	type ScopeOrigin,
	type EnhancedScope,
	type ScopeGroup,
	type ScopeVocabulary,
	VOCABULARY_NOUNS,
	extractResourceFromScope,
	formatResourceName,
	groupScopesByResource,
	scopesInGroup,
	filterScopeGroups,
} from '@/shared/lib/scopes';

// The shell scrolls `<main>`, not the window: read and drive page scroll here.
export { shellScroller, shellScrollRoot, shellScrollTop } from '@/shared/lib/shellScroll';
export {
	SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR,
	RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE,
	retiredServiceAccountLabel,
	MIGRATED_SERVICE_ACCOUNT_KEY_WARNING,
	holdsMigratedServiceAccountKey,
} from '@/shared/lib/retiredActors';

// Narrow, module-consumable slices of the agent-stream data layer (NOT the
// rail's React components): the HAL-link id parser (so Monitor's Events
// drill-in and the rail parse links with the same rules) and the
// provider-optional stream hook (so Monitor can read the rail's in-memory feed
// when the shell's stream is mounted, and no-op in
// tests/embedded surfaces where it isn't).
export { useAgentStreamOptional } from '@/shared/lib/agentStream';

// The event → UI adaptation and its wording helpers, so Monitor's Activity
// feed reads an event exactly the way the rail and toasts do (same kind
// label, same day separators, same "where does this lead" destination).
export {
	adaptEvent,
	primaryDestinationFor,
	STREAM_KIND_LABEL,
	formatStreamDayLabel,
	formatStreamTime,
	streamDayKey,
	isFailureSeverity,
	isRetiredEventType,
	EVENTS_FORBIDDEN_COPY,
} from '@/shared/lib/agentStream';
export type { StreamEvent, StreamKind, StreamSeverity } from '@/shared/lib/agentStream';

// API-identity display helpers — one humanising rule applied everywhere a
// machine identity (`api_id` / `api_vendor` / `api_name`) needs to render as a
// friendly primary line — shared so Discover, the credential picker, and the
// binding surfaces all apply the same rule (implementation: `api-display.ts`).
export {
	titleFromApiId,
	apiRefDisplayName,
	apiIdentityTuple,
	workspaceApiTitle,
	vendorIconPropsFor,
	formatApiVersion,
	formatOperation,
} from '@/shared/lib/api-display';

// Whether a stored trace id can open a trace (the backend's `"unknown"`
// placeholder and empty ids can't) — Monitor and the Agents page agree on it.
export { hasTrace } from '@/shared/lib/trace';

// The compact relative age ("3m", "4d") a module's own helpers build on.
export { timeAgo } from '@/shared/lib/utils';
