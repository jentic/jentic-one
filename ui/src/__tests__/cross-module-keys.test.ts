import { describe, expect, it } from 'vitest';
import { sharedQueryKeys } from '@/shared/api';
import { workspaceKeys } from '@/modules/workspace/api/hooks';
import { dashboardKeys } from '@/modules/dashboard/api';
import { pendingAccessRequestCountKey } from '@/shared/hooks/usePendingAccessRequestCount';
import { pendingAgentsCountKey } from '@/shared/hooks/usePendingAgentsCount';
import { actorDirectoryKey } from '@/shared/hooks/useActorDirectory';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';

/**
 * Cross-module key-registry guard (#511).
 *
 * Discover can't import the Workspace module (ESLint sibling-module boundary),
 * so it invalidates the workspace API list through the shared cross-module key
 * registry (`sharedQueryKeys.workspaceApis`) instead of a hand-synced literal.
 * The Workspace module's own `workspaceKeys.apis()` derives from the same
 * registry root, so there's a single definition. This test lives outside both
 * modules — it asserts the owning factory still resolves to the shared root, so
 * a refactor that forks them (reintroducing the stale-list bug) fails here.
 */
describe('cross-module query-key registry', () => {
	it('workspaceKeys.apis() derives from sharedQueryKeys.workspaceApis', () => {
		expect([...workspaceKeys.apis()]).toEqual([...sharedQueryKeys.workspaceApis]);
	});

	it('dashboardKeys.all derives from sharedQueryKeys.dashboardRoot', () => {
		// The shared SSE→query bridge (`agentStream`) invalidates
		// `sharedQueryKeys.dashboardRoot` on every approval event to refresh the
		// dashboard tiles. If the Dashboard's own root forked from the shared
		// one, that invalidation would silently miss — this pins them to one
		// definition.
		expect([...dashboardKeys.all]).toEqual([...sharedQueryKeys.dashboardRoot]);
	});

	it('pendingAccessRequestCountKey sits under sharedQueryKeys.accessRequestsRoot', () => {
		// Every decision path invalidates `accessRequestsRoot` (a prefix). The
		// nav badge must live UNDER that prefix or it would never refresh — this
		// pins the badge key to the shared root so a prefix invalidation always
		// catches it (the original stale-badge failure mode).
		const root = sharedQueryKeys.accessRequestsRoot;
		expect([...pendingAccessRequestCountKey].slice(0, root.length)).toEqual([...root]);
	});

	it('agentsKeys.all derives from sharedQueryKeys.agentsRoot', () => {
		// The agents approve/deny/create mutations invalidate
		// `sharedQueryKeys.agentsRoot` to refresh the pending-agents nav badge.
		// If the Agents module's own root forked from the shared one, that
		// invalidation would miss the badge — this pins them to one definition.
		expect([...agentsKeysForTest.all]).toEqual([...sharedQueryKeys.agentsRoot]);
	});

	it('pendingAgentsCountKey sits under sharedQueryKeys.agentsRoot', () => {
		// The pending-agents nav badge must live UNDER the agents root so a
		// prefix invalidation on approve/deny always refreshes it (#652).
		const root = sharedQueryKeys.agentsRoot;
		expect([...pendingAgentsCountKey].slice(0, root.length)).toEqual([...root]);
	});

	it('actorDirectoryKey derives from sharedQueryKeys.actorDirectoryRoot', () => {
		// The SSE→query bridge invalidates `actorDirectoryRoot` when an agent
		// registers, so surfaces resolving the new agent's `actor_id` (rail
		// rows, the provisioning wizard's badge + toolkit-name suggestion)
		// refetch instead of rendering the raw `agnt_…` id until the 5-minute
		// staleTime expires. A forked key would silently miss that refresh.
		expect([...actorDirectoryKey]).toEqual([...sharedQueryKeys.actorDirectoryRoot]);
	});
});
