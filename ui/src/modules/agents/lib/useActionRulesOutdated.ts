/**
 * Whether a binding's action rules name an action the API's current version
 * no longer has — the "Rules out of date" state the tile's row and card show,
 * not only Manage access. Such a rule matches nothing at the broker, so a call
 * it denied or held falls through to the rules after it; nothing else on the
 * page would say so.
 *
 * Reads only for a binding whose rules name actions, through the same caches
 * Manage access reads (the rules, the API's operations and its documents).
 */
import { useMemo } from 'react';
import { useAgentBindingEffectiveRules } from '@/modules/agents/api';
import { useActionCatalog, type SiblingApiReference } from '@/shared/credentials/components';
import { describeActions } from '@/shared/credentials/lib/actionRules';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

const NONE: readonly SiblingApiReference[] = [];

export function useActionRulesOutdated(
	agentId: string,
	tile: ApiTileModel,
	/** The other APIs the same rule list governs (see `siblingApiReferences`). */
	siblings: readonly SiblingApiReference[] = NONE,
): boolean {
	const effective = useAgentBindingEffectiveRules(agentId, tile.credentialId);
	const ids = useMemo(
		() => (effective.rules ?? []).filter((r) => !r._system).flatMap((r) => r.operations ?? []),
		[effective.rules],
	);
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
		ruleIds: ids,
	});
	return useMemo(
		() => ids.length > 0 && describeActions(ids, catalog).missing > 0,
		[ids, catalog],
	);
}
