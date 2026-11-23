/**
 * Who else a binding's rules govern. Permission rules belong to the binding
 * (agent × credential), not to an API: a credential that reaches several APIs
 * — every version of one API, or every API of a vendor — shares ONE rule list
 * across all of them, including APIs imported later. From one tile's point
 * of view this names the other API rows that share the rules today, never the
 * tile itself.
 */
import type { SiblingApiReference } from '@/shared/credentials/components';
import { joinAnd } from '@/shared/credentials/lib/actionRules';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

function sameApi(a: ApiTileModel, b: ApiTileModel): boolean {
	return a.vendor === b.vendor && (a.apiName ?? '') === (b.apiName ?? '');
}

/** The OTHER tiles one binding fans out to. */
export function siblingTiles(open: ApiTileModel, tiles: readonly ApiTileModel[]): ApiTileModel[] {
	return tiles.filter((t) => t.bindingId === open.bindingId && t.key !== open.key);
}

/** A sibling's label: its title, with its version when another tile of the
 * binding (the open one included) carries the same title. */
export function siblingLabel(
	tile: ApiTileModel,
	open: ApiTileModel,
	siblings: readonly ApiTileModel[],
): string {
	const repeats =
		tile.title === open.title ||
		siblings.some((s) => s.key !== tile.key && s.title === tile.title);
	return repeats && tile.version ? `${tile.title} ${tile.version}` : tile.title;
}

/**
 * The other API rows sharing these rules today ("Archive", "other versions of
 * this API") — what the shared-rules note under Credential names. Other APIs
 * by title (a version appended when a title alone would repeat), never the
 * open API. Null when the rules govern no other imported API: an API that
 * isn't imported yet shares nothing until it is.
 */
export function otherApisPhrase(
	open: ApiTileModel,
	siblings: readonly ApiTileModel[],
): string | null {
	const parts = [
		...new Set(
			siblings.filter((s) => !sameApi(s, open)).map((s) => siblingLabel(s, open, siblings)),
		),
	];
	if (siblings.some((s) => sameApi(s, open))) parts.push('other versions of this API');
	return parts.length > 0 ? joinAnd(parts) : null;
}

/** The siblings as the action catalog reads them — only imported ones have a
 * version, so only they have operations to name. */
export function siblingApiReferences(
	open: ApiTileModel,
	siblings: readonly ApiTileModel[],
): SiblingApiReference[] {
	return siblings
		.filter((s) => s.version)
		.map((s) => ({
			key: s.key,
			title: siblingLabel(s, open, siblings),
			api: { vendor: s.vendor, name: s.apiName, version: s.version },
		}));
}
