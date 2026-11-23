/**
 * The identities an API's operation carries, read from an OpenAPI document.
 *
 * Every operation has two: the registry id (`op_…`), a hash of the revision,
 * the path and the method — so every revision gives it a new one — and, when
 * the spec declares it, the spec's `operationId` (`createOrder`), which stays
 * the same across re-imports. The broker matches an action rule on either.
 * Framework-free: the hooks in `api/action-identity-hooks.ts` feed these.
 */

/** The methods an OpenAPI path item declares operations under. */
const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'];

/** The registry stores at most this many characters of an `operationId`. */
const STORED_OPERATION_ID_MAX = 255;

/** One action as its spec declares it. */
export interface SpecAction {
	method: string;
	path: string;
	/** The spec's `operationId`, as written; null when it declares none. */
	operationId: string | null;
	/** The spec's summary, for naming an action of an earlier version. */
	summary: string | null;
}

/** `POST /orders` — the method and path template that say which action this is. */
export function actionAt(method: string, path: string): string {
	return `${method.toUpperCase()} ${path}`;
}

/** Every operation an OpenAPI document declares, in document order. */
export function specActions(spec: unknown): SpecAction[] {
	const paths = (spec as { paths?: unknown } | null)?.paths;
	if (paths == null || typeof paths !== 'object') return [];
	const actions: SpecAction[] = [];
	for (const [path, item] of Object.entries(paths as Record<string, unknown>)) {
		if (item == null || typeof item !== 'object') continue;
		for (const method of HTTP_METHODS) {
			const op = (item as Record<string, unknown>)[method];
			if (op == null || typeof op !== 'object') continue;
			const { operationId, summary } = op as { operationId?: unknown; summary?: unknown };
			actions.push({
				method: method.toUpperCase(),
				path,
				operationId: typeof operationId === 'string' ? operationId : null,
				summary: typeof summary === 'string' && summary.trim() ? summary.trim() : null,
			});
		}
	}
	return actions;
}

/**
 * The `operationId`s the registry stores unchanged, by action: declared,
 * not blank, short enough to store whole, and used by one operation only. The
 * registry renames a repeated id (`<id>_2`) in an order this can't see, so a
 * repeated one is left out rather than guessed.
 */
export function storedOperationIds(actions: readonly SpecAction[]): Map<string, string> {
	const uses = new Map<string, number>();
	for (const a of actions) {
		if (a.operationId) uses.set(a.operationId, (uses.get(a.operationId) ?? 0) + 1);
	}
	const byAction = new Map<string, string>();
	for (const a of actions) {
		const id = a.operationId;
		if (!id || !id.trim() || id.length > STORED_OPERATION_ID_MAX || uses.get(id) !== 1)
			continue;
		byAction.set(actionAt(a.method, a.path), id);
	}
	return byAction;
}

/**
 * The registry id an operation of `revisionId` was given: `op_` and the first
 * 40 hex characters of SHA-256 over `revision:path:METHOD`, as the registry
 * computes it. Lets an id of an earlier revision be named as the action it was.
 */
export async function registryOperationId(
	revisionId: string,
	path: string,
	method: string,
): Promise<string> {
	const bytes = new TextEncoder().encode(`${revisionId}:${path}:${method.toUpperCase()}`);
	const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
	const hex = Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
	return `op_${hex.slice(0, 40)}`;
}
