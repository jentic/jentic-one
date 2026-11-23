/**
 * What the agent's API row and card say about a binding's action rules beyond
 * their counts — what only Manage access said before:
 *
 * - `outdated`: a rule names an action the API's current version no longer
 *   has. It matches nothing at the broker, so a call it denied or held falls
 *   through to the rules after it ("Rules out of date").
 * - `ambiguous`: a rule names an action whose `operationId` another API the
 *   credential covers declares too, in a way the broker can't settle — an Ask
 *   or Allow by the shared name, or the action named in only some of the APIs
 *   ("Rules ambiguous"; see `ruleAmbiguity`).
 *
 * Reads only for a binding whose rules name actions, through the same caches
 * Manage access reads (the rules, the API's operations and its documents).
 */
import { useMemo } from 'react';
import { useAgentBindingEffectiveRules } from '@/modules/agents/api';
import { useActionCatalog, type SiblingApiReference } from '@/shared/credentials/components';
import { describeActions, ruleAmbiguity } from '@/shared/credentials/lib/actionRules';
import { apiScopeReach } from '@/shared/credentials/lib/apiIdentity';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

const NONE: readonly SiblingApiReference[] = [];
const GROUP: Record<string, 'allow' | 'ask' | 'deny'> = {
	allow: 'allow',
	'require-approval': 'ask',
	deny: 'deny',
};

export interface ActionRulesHealth {
	outdated: boolean;
	ambiguous: boolean;
}

export function useActionRulesHealth(
	agentId: string,
	tile: ApiTileModel,
	/** The other APIs the same rule list governs (see `siblingApiReferences`). */
	siblings: readonly SiblingApiReference[] = NONE,
): ActionRulesHealth {
	const effective = useAgentBindingEffectiveRules(agentId, tile.credentialId);
	const actionRules = useMemo(
		() =>
			(effective.rules ?? []).filter(
				(r) => !r._system && (r.operations ?? []).length > 0 && GROUP[r.effect],
			),
		[effective.rules],
	);
	const ids = useMemo(() => actionRules.flatMap((r) => r.operations ?? []), [actionRules]);
	const api = useMemo(
		() =>
			tile.version && tile.apiName
				? { vendor: tile.vendor, name: tile.apiName, version: tile.version }
				: null,
		[tile.vendor, tile.apiName, tile.version],
	);
	const catalog = useActionCatalog(api, {
		enabled: ids.length > 0,
		siblings,
		reach: apiScopeReach(tile.scope),
		ruleIds: ids,
	});
	return useMemo(() => {
		if (ids.length === 0) return { outdated: false, ambiguous: false };
		const ambiguous = actionRules.some((r) => {
			const found = ruleAmbiguity(r.operations ?? [], GROUP[r.effect]!, catalog);
			return found.shared.length > 0 || found.partial.length > 0;
		});
		return { outdated: describeActions(ids, catalog).missing > 0, ambiguous };
	}, [ids, actionRules, catalog]);
}
