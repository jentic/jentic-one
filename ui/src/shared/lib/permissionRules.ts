/**
 * Permission-rule DISPLAY primitives — the typed shape of a broker permission
 * rule plus the shared humanising helpers every rules surface renders with
 * (the agent-detail binding permissions editor/tester and the rail's
 * `OperationsSummary`/`OperationsDialog`).
 *
 * Broker rules are ordered, first-match-wins, default-deny, keyed on the
 * `(agent, credential)` binding. These helpers only DESCRIBE rules; parsing,
 * editing, and persistence live with their owning surfaces.
 */

/** Permission-rule effect — the broker only enforces `allow` and `deny`. */
export type PermissionRuleEffect = 'allow' | 'deny';

/** How a rule's `path` is interpreted by the broker. Absent = `regex`. */
export type PermissionRuleMatchMode = 'regex' | 'prefix' | 'exact';

/**
 * A single permission rule on an `(agent, credential)` binding — enforced by
 * the broker (ordered, first-match-wins, default-deny), so an allow/deny list
 * of rules is effectively an exact grant of operations.
 */
export interface PermissionRule {
	effect: PermissionRuleEffect;
	/** HTTP methods the rule matches (e.g. `["GET","POST"]`); null = any. */
	methods?: string[] | null;
	/** Path/regex the rule matches; null = any. */
	path?: string | null;
	/**
	 * How `path` is matched (`regex` full-match, literal `prefix`, literal
	 * `exact`). null/absent = regex — the backend default.
	 */
	match_mode?: PermissionRuleMatchMode | null;
	/** OpenAPI operationIds the rule matches; null = any. */
	operations?: string[] | null;
}

/**
 * True when a rule is an UNRESTRICTED allow — effect `allow` with no methods,
 * path, or operations to constrain it. Under the broker's first-match-wins,
 * default-deny evaluation such a rule matches every request, so it grants
 * blanket access. The API now rejects these (a condition-less `allow` is a 422),
 * but a reviewer can still encounter one on a historical binding, so the UI
 * flags it loudly rather than rendering it like an innocuous allow.
 */
export function isUnrestrictedAllow(rule: PermissionRule): boolean {
	return (
		rule.effect === 'allow' && !rule.methods?.length && !rule.path && !rule.operations?.length
	);
}

/**
 * A short, screen-reader-friendly sentence describing what a rule set grants —
 * e.g. "Allows GET, POST on 3 operations". Used as the `aria-label` so SR users
 * get the gist without parsing individual chips.
 *
 * Restrictive (`deny`) rules are summarised FIRST so an SR
 * user hears what is blocked before the (often longer) allow enumeration — the
 * block is the security-critical signal. An UNRESTRICTED allow is the other
 * security-critical signal, so it is called out explicitly as "unrestricted".
 */
export function ruleSummary(rules: PermissionRule[]): string {
	if (rules.length === 0) return 'No operation restrictions — full access to the resource.';
	const ordered = [...rules].sort(
		(a, b) => (a.effect === 'allow' ? 1 : 0) - (b.effect === 'allow' ? 1 : 0),
	);
	const parts = ordered.map((rule) => {
		// An unrestricted allow matches everything — surface that danger plainly
		// instead of the bland "Allows all requests".
		if (isUnrestrictedAllow(rule)) return 'Allows ANY request (unrestricted)';
		const verb = rule.effect === 'allow' ? 'Allows' : 'Blocks';
		const bits: string[] = [];
		if (rule.methods?.length) bits.push(rule.methods.join(', '));
		if (rule.operations?.length) {
			bits.push(
				`${rule.operations.length} operation${rule.operations.length === 1 ? '' : 's'}`,
			);
		}
		const head = bits.length ? `${verb} ${bits.join(' on ')}` : `${verb} all requests`;
		// Path is a separate scope, not another thing the methods/ops act "on" —
		// append it with its own clause, phrased per match mode so a prefix rule
		// never reads like a regex (or vice versa).
		return rule.path ? `${head}, ${pathClause(rule.path, rule.match_mode)}` : head;
	});
	return parts.join('; ') + '.';
}

/** The path-scope clause of a rule summary, phrased for the rule's match mode. */
function pathClause(path: string, mode: PermissionRuleMatchMode | null | undefined): string {
	switch (mode) {
		case 'prefix':
			return `scoped to paths starting with ${path}`;
		case 'exact':
			return `scoped to exactly path ${path}`;
		default:
			return `scoped to path ${path}`;
	}
}
