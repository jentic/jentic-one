/**
 * Rules of a CLI-profile credential (one connect covering several APIs of a
 * vendor, e.g. `jentic connect github`), grouped by the profile API each rule
 * applies to.
 */
import type {
	PermissionRule,
	ProfileApi,
	ProfileApiRole,
} from '@/shared/credentials/api/vendors-types';

/** Heading label per profile API role. */
export const PROFILE_API_ROLE_LABEL: Record<ProfileApiRole, string> = {
	rest: 'REST API',
	graphql: 'GraphQL',
	git: 'git',
	uploads: 'Uploads',
};

/**
 * The profile API a rule applies to: its `api_role`, or, without one, its
 * kind — a `git` rule applies to `git`, a `graphql` rule to `graphql`, any
 * other rule to `rest`.
 */
export function ruleApiRole(rule: PermissionRule): ProfileApiRole {
	if (rule.api_role) return rule.api_role;
	if (rule.match_mode === 'git') return 'git';
	if (rule.match_mode === 'graphql') return 'graphql';
	return 'rest';
}

/** The rule targeted at `role`; a rule already resolving to `role` is returned as is. */
export function withApiRole(rule: PermissionRule, role: ProfileApiRole): PermissionRule {
	return ruleApiRole(rule) === role ? rule : { ...rule, api_role: role };
}

/** One section of the grouped rules view: a role and the profile APIs that play it. */
export interface ProfileRuleGroup {
	role: ProfileApiRole;
	apis: ProfileApi[];
	rules: PermissionRule[];
}

/**
 * Split `rules` into one group per role the profile declares, in declaration
 * order. Rules for a role the profile does not declare are returned apart, so
 * nothing is dropped.
 */
export function groupProfileRules(
	rules: readonly PermissionRule[],
	profileApis: readonly ProfileApi[],
): { groups: ProfileRuleGroup[]; other: PermissionRule[] } {
	const groups: ProfileRuleGroup[] = [];
	for (const api of profileApis) {
		const existing = groups.find((g) => g.role === api.role);
		if (existing) existing.apis.push(api);
		else groups.push({ role: api.role, apis: [api], rules: [] });
	}
	const other: PermissionRule[] = [];
	for (const rule of rules) {
		const group = groups.find((g) => g.role === ruleApiRole(rule));
		if (group) group.rules.push(rule);
		else other.push(rule);
	}
	return { groups, other };
}

/** The full rule list after one group's rules change: groups in order, then the rest. */
export function replaceGroupRules(
	groups: readonly ProfileRuleGroup[],
	other: readonly PermissionRule[],
	role: ProfileApiRole,
	next: readonly PermissionRule[],
): PermissionRule[] {
	return [
		...groups.flatMap((g) =>
			g.role === role ? next.map((r) => withApiRole(r, role)) : g.rules,
		),
		...other,
	];
}
