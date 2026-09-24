/**
 * specDiff — structural diff between two resolved OpenAPI documents.
 *
 * Powers the spec viewer's default diff mode: instead of dumping ~800 lines of
 * raw JSON, we walk both documents and emit one entry per changed subtree
 * (`$.servers`, `$.info.version`, `$.paths['/pet'].get`, …) with its
 * before/after values — so the changed section IS what the dialog shows, no
 * scrolling required. Pure + unit-tested; no I/O.
 *
 * Granularity: objects are recursed into so sibling keys don't drown a small
 * change; arrays and scalars are leaves (an array change reads better as one
 * "before → after" block than as index-by-index noise). Entries are capped so
 * a pathological "everything changed" pair can't render an unbounded list.
 */

export type SpecDiffKind = 'added' | 'removed' | 'changed';

export interface SpecDiffEntry {
	/** JSONPath-ish location, e.g. `$.servers` or `$.paths['/pet'].get`. */
	path: string;
	kind: SpecDiffKind;
	/** Value in the base document (absent for `added`). */
	before?: unknown;
	/** Value in the target document (absent for `removed`). */
	after?: unknown;
}

export interface SpecDiffResult {
	entries: SpecDiffEntry[];
	/** True when the walk hit the entry cap and stopped early. */
	truncated: boolean;
}

const DEFAULT_MAX_ENTRIES = 200;

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return value != null && typeof value === 'object' && !Array.isArray(value);
}

/** Append a key to a JSONPath-ish path, bracket-quoting non-identifier keys. */
function childPath(parent: string, key: string): string {
	return /^[A-Za-z0-9_-]+$/.test(key) ? `${parent}.${key}` : `${parent}['${key}']`;
}

/** Deep equality via stable traversal (JSON-shaped values only). */
function deepEqual(a: unknown, b: unknown): boolean {
	if (Object.is(a, b)) return true;
	if (Array.isArray(a) && Array.isArray(b)) {
		return a.length === b.length && a.every((item, i) => deepEqual(item, b[i]));
	}
	if (isPlainObject(a) && isPlainObject(b)) {
		const aKeys = Object.keys(a);
		const bKeys = Object.keys(b);
		return (
			aKeys.length === bKeys.length &&
			aKeys.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]))
		);
	}
	return false;
}

/**
 * Diff `base` → `target`. Returns changed/added/removed subtrees, depth-first
 * in the target's key order (so `info` / `servers` — the usual overlay
 * targets — surface before the long `paths` tail).
 */
export function diffSpecs(
	base: unknown,
	target: unknown,
	options: { maxEntries?: number } = {},
): SpecDiffResult {
	const maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;
	const entries: SpecDiffEntry[] = [];
	let truncated = false;

	function push(entry: SpecDiffEntry): boolean {
		if (entries.length >= maxEntries) {
			truncated = true;
			return false;
		}
		entries.push(entry);
		return true;
	}

	function walk(path: string, before: unknown, after: unknown): boolean {
		if (deepEqual(before, after)) return true;
		if (isPlainObject(before) && isPlainObject(after)) {
			// Removed keys first (in base order), then added/changed in target order.
			// Own-property checks (never `in`): specs are JSON.parse output where
			// keys like `constructor`/`toString` legitimately appear as schema
			// property names, and `in` would resolve them via Object.prototype —
			// silently dropping removals and mis-reporting additions.
			const has = (obj: Record<string, unknown>, key: string) =>
				Object.prototype.hasOwnProperty.call(obj, key);
			for (const key of Object.keys(before)) {
				if (!has(after, key)) {
					if (!push({ path: childPath(path, key), kind: 'removed', before: before[key] }))
						return false;
				}
			}
			for (const key of Object.keys(after)) {
				if (!has(before, key)) {
					if (!push({ path: childPath(path, key), kind: 'added', after: after[key] }))
						return false;
				} else if (!walk(childPath(path, key), before[key], after[key])) {
					return false;
				}
			}
			return true;
		}
		return push({ path, kind: 'changed', before, after });
	}

	walk('$', base, target);
	return { entries, truncated };
}
