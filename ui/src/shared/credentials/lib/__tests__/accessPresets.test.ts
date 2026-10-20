/**
 * The access presets every bind surface offers, and the rules each one writes —
 * the workspace bind dialog and the Add-APIs queue both save exactly these.
 */
import { describe, it, expect } from 'vitest';
import {
	ALLOW_ALL_DESCRIPTION,
	actionRule,
	coverageNote,
	draftForRules,
	draftRulesForPreset,
	isActionRule,
	joinCustomRules,
	presetForRules,
	presetOptions,
	rulesForPreset,
	splitCustomRules,
} from '@/shared/credentials/lib/accessPresets';
import { explainRules } from '@/shared/credentials/lib/rule-matcher';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

const GET_REPOS: PermissionRule = { effect: 'allow', methods: ['GET'], path: '/repos' };

describe('rulesForPreset', () => {
	it('Allow all is one regex `.*` allow — never a condition-less one (a 422)', () => {
		expect(rulesForPreset('all', [])).toEqual([{ effect: 'allow', path: '.*' }]);
	});

	it('Read-only is GET on any path', () => {
		expect(rulesForPreset('read', [])).toEqual([{ effect: 'allow', methods: ['GET'] }]);
	});

	it('Read only, ask before anything else: the GET allow, then a catch-all Ask', () => {
		// Ask needs a condition, so the catch-all is the regex `.*` (the default
		// mode, so the cleaned rule carries no `match_mode`).
		expect(rulesForPreset('read-ask', [])).toEqual([
			{ effect: 'allow', methods: ['GET'] },
			{ effect: 'require-approval', path: '.*' },
		]);
	});

	it('a By action rule (operation ids, no method or path) is a valid Custom rule', () => {
		// The backend's "constrain at least one" check accepts an allow with only operation ids.
		expect(
			rulesForPreset('custom', [
				{ effect: 'allow', methods: null, path: null, operations: ['repos/get'] },
			]),
		).toEqual([{ effect: 'allow', operations: ['repos/get'] }]);
		expect(
			rulesForPreset('custom', [
				{ effect: 'allow', methods: null, path: null, operations: [] },
			]),
		).toBeNull();
	});

	it('Custom saves the rules as written, in order, cleaned of empty conditions', () => {
		const deny: PermissionRule = {
			effect: 'deny',
			methods: [],
			path: '/admin',
			match_mode: 'prefix',
		};
		expect(rulesForPreset('custom', [deny, GET_REPOS])).toEqual([
			{ effect: 'deny', path: '/admin', match_mode: 'prefix' },
			{ effect: 'allow', methods: ['GET'], path: '/repos' },
		]);
	});

	it('is incomplete with no preset, an empty Custom list, or an empty allow row', () => {
		expect(rulesForPreset(null, [GET_REPOS])).toBeNull();
		expect(rulesForPreset('custom', [])).toBeNull();
		expect(rulesForPreset('custom', [GET_REPOS, { effect: 'allow' }])).toBeNull();
	});

	it('ignores a stale custom draft once a preset is picked', () => {
		expect(rulesForPreset('read', [GET_REPOS])).toEqual([
			{ effect: 'allow', methods: ['GET'] },
		]);
	});
});

describe('presetOptions / coverageNote', () => {
	it('four presets, least access first; Full access says how far the credential reaches', () => {
		for (const reach of ['pinned', 'any-version', 'vendor-wide'] as const) {
			const options = presetOptions(reach);
			expect(options.map((o) => o.value)).toEqual(['read', 'read-ask', 'all', 'custom']);
			expect(options[1]).toMatchObject({
				label: 'Read only, ask before anything else',
				description: 'GET requests run; any other call waits for approval',
			});
			expect(options[2]).toMatchObject({
				label: 'Full access',
				description: ALLOW_ALL_DESCRIPTION[reach],
			});
			expect(options[3]).toMatchObject({
				label: 'Custom rules',
				description: 'By action or by path, in order',
			});
		}
	});

	it('only a credential reaching past one pinned API carries a coverage note', () => {
		expect(coverageNote('pinned')).toBeNull();
		expect(coverageNote('any-version')).toMatch(/future versions too/);
		expect(coverageNote('vendor-wide')).toMatch(/every API of its vendor/);
	});
});

describe('splitCustomRules / joinCustomRules — the two editors, one saved list', () => {
	const ACTION: PermissionRule = {
		effect: 'allow',
		methods: null,
		path: null,
		operations: ['repos/get'],
	};

	it('an operations-only allow is the action half; everything else is the path half', () => {
		expect(isActionRule(ACTION)).toBe(true);
		// A rule that also names a path belongs to the path editor, which preserves its operations.
		expect(isActionRule({ ...ACTION, path: '/repos' })).toBe(false);
		expect(isActionRule({ ...ACTION, methods: ['GET'] })).toBe(false);
		expect(isActionRule({ ...ACTION, effect: 'deny' })).toBe(true);
		expect(isActionRule({ ...ACTION, effect: 'require-approval' })).toBe(true);
		expect(isActionRule({ ...ACTION, operations: [] })).toBe(false);
		expect(isActionRule(GET_REPOS)).toBe(false);

		expect(splitCustomRules([GET_REPOS, ACTION])).toEqual({
			pathRules: [GET_REPOS],
			actionRules: [ACTION],
			groups: { allow: ['repos/get'], ask: [], deny: [] },
			denyMovesAhead: false,
		});
	});

	it('actionRule writes the shape the broker matches on `operations`', () => {
		expect(actionRule(['a', 'b'])).toEqual({
			effect: 'allow',
			methods: null,
			path: null,
			operations: ['a', 'b'],
		});
	});

	const ONLY_ALLOW = { allow: ['repos/get'], ask: [], deny: [] };

	it('re-joins with the Allow action rule after the path rules, which keep their order', () => {
		expect(joinCustomRules([GET_REPOS], ONLY_ALLOW)).toEqual([GET_REPOS, ACTION]);
		const rules = [GET_REPOS, ACTION, { effect: 'deny' as const, path: '/admin' }];
		const { pathRules, groups } = splitCustomRules(rules);
		expect(joinCustomRules(pathRules, groups)).toEqual([
			GET_REPOS,
			{ effect: 'deny', path: '/admin' },
			ACTION,
		]);
	});

	it('an action rule is never the "empty allow" that holds Custom incomplete', () => {
		expect(rulesForPreset('custom', joinCustomRules([GET_REPOS], ONLY_ALLOW))).toEqual([
			{ effect: 'allow', methods: ['GET'], path: '/repos' },
			{ effect: 'allow', operations: ['repos/get'] },
		]);
		expect(
			rulesForPreset('custom', joinCustomRules([{ effect: 'allow' }], ONLY_ALLOW)),
		).toBeNull();
	});
});

describe('draftRulesForPreset + explainRules — the dry run of an unsaved choice', () => {
	const ask = (rules: PermissionRule[], method: string, path: string) =>
		explainRules(rules, { method, path, operation_id: null });

	it('Read-only: GET allowed by rule 0, POST default-denied', () => {
		const rules = draftRulesForPreset('read', []);
		expect(ask(rules, 'GET', '/repos/x')).toEqual({
			allowed: true,
			effect: 'allow',
			matched: true,
			ruleIndex: 0,
		});
		expect(ask(rules, 'POST', '/repos/x')).toEqual({
			allowed: false,
			effect: 'deny',
			matched: false,
			ruleIndex: null,
		});
	});

	it('Allow all: any method, any path', () => {
		const rules = draftRulesForPreset('all', []);
		expect(ask(rules, 'DELETE', '/anything').allowed).toBe(true);
	});

	it('Custom: first match wins, and an incomplete draft is still probed as typed', () => {
		const rules = draftRulesForPreset('custom', [
			{ effect: 'deny', path: '/repos/secret', match_mode: 'prefix' },
			{ effect: 'allow', path: '/repos', match_mode: 'prefix' },
			{ effect: 'allow' },
		]);
		expect(ask(rules, 'GET', '/repos/secret/1')).toEqual({
			allowed: false,
			effect: 'deny',
			matched: true,
			ruleIndex: 0,
		});
		expect(ask(rules, 'GET', '/repos/open')).toEqual({
			allowed: true,
			effect: 'allow',
			matched: true,
			ruleIndex: 1,
		});
		// The empty allow row never grants (the broker skips a condition-less allow).
		expect(ask(rules, 'GET', '/users').matched).toBe(false);
	});

	it('no preset at all is no rules — default deny', () => {
		expect(ask(draftRulesForPreset(null, [GET_REPOS]), 'GET', '/repos').allowed).toBe(false);
	});
});

describe('By action — each action Allow, Ask, Deny or unset', () => {
	const none = { allow: [], ask: [], deny: [] };
	const DENY_ALL: PermissionRule = { effect: 'deny', path: '/admin', match_mode: 'prefix' };

	it('saves Deny first, then the path rules, then Ask, then Allow', () => {
		expect(
			joinCustomRules([GET_REPOS, DENY_ALL], { allow: ['a', 'b'], ask: ['c'], deny: ['d'] }),
		).toEqual([
			{ effect: 'deny', methods: null, path: null, operations: ['d'] },
			GET_REPOS,
			DENY_ALL,
			{ effect: 'require-approval', methods: null, path: null, operations: ['c'] },
			{ effect: 'allow', methods: null, path: null, operations: ['a', 'b'] },
		]);
		expect(actionRule(['c'], 'require-approval').effect).toBe('require-approval');
		expect(actionRule(['d'], 'deny').effect).toBe('deny');
	});

	it('an effect with no actions saves no rule; nothing set saves only the path rules', () => {
		expect(joinCustomRules([], { ...none, allow: ['a'] })).toEqual([actionRule(['a'])]);
		expect(joinCustomRules([], { ...none, ask: ['a'] })).toEqual([
			actionRule(['a'], 'require-approval'),
		]);
		expect(joinCustomRules([GET_REPOS], { ...none, deny: ['a'] })).toEqual([
			actionRule(['a'], 'deny'),
			GET_REPOS,
		]);
		// Unset never saves a Deny: no actions set is no action rule at all.
		expect(joinCustomRules([GET_REPOS], none)).toEqual([GET_REPOS]);
	});

	it('an action is in one set at most — Deny, then Ask, then Allow, as the broker reaches them', () => {
		expect(joinCustomRules([], { allow: ['a', 'b'], ask: ['b', 'c'], deny: ['c'] })).toEqual([
			actionRule(['c'], 'deny'),
			actionRule(['b'], 'require-approval'),
			actionRule(['a']),
		]);
	});

	it('round-trips: split a saved list and join it back unchanged', () => {
		const rules: PermissionRule[] = [
			actionRule(['d'], 'deny'),
			GET_REPOS,
			actionRule(['c'], 'require-approval'),
			actionRule(['a', 'b']),
		];
		const { pathRules, groups, denyMovesAhead } = splitCustomRules(rules);
		expect(pathRules).toEqual([GET_REPOS]);
		expect(groups).toEqual({ allow: ['a', 'b'], ask: ['c'], deny: ['d'] });
		expect(denyMovesAhead).toBe(false);
		expect(joinCustomRules(pathRules, groups)).toEqual(rules);
	});

	it('rules saved in the old shape still load: one allow rule, or several', () => {
		expect(splitCustomRules([GET_REPOS, actionRule(['a'])]).groups).toEqual({
			allow: ['a'],
			ask: [],
			deny: [],
		});
		expect(
			splitCustomRules([actionRule(['a']), GET_REPOS, actionRule(['b', 'a'])]).groups,
		).toEqual({ allow: ['a', 'b'], ask: [], deny: [] });
	});

	it('an id in several action rules takes the effect of the earliest (first match wins)', () => {
		// Shouldn't happen from the editor, but can from manual edits.
		expect(
			splitCustomRules([actionRule(['a', 'b']), actionRule(['b', 'c'], 'require-approval')])
				.groups,
		).toEqual({ allow: ['a', 'b'], ask: ['c'], deny: [] });
		expect(
			splitCustomRules([actionRule(['b'], 'require-approval'), actionRule(['a', 'b'])])
				.groups,
		).toEqual({ allow: ['a'], ask: ['b'], deny: [] });
		expect(
			splitCustomRules([actionRule(['b'], 'deny'), actionRule(['b'], 'require-approval')])
				.groups,
		).toEqual({ allow: [], ask: [], deny: ['b'] });
	});

	it('a Deny action rule AFTER the path rules loads as Deny, flagged: saving moves it ahead', () => {
		const late = [GET_REPOS, actionRule(['x'], 'deny')];
		const { groups, denyMovesAhead, pathRules } = splitCustomRules(late);
		expect(groups.deny).toEqual(['x']);
		expect(denyMovesAhead).toBe(true);
		expect(joinCustomRules(pathRules, groups)).toEqual([actionRule(['x'], 'deny'), GET_REPOS]);
		// With no path rule ahead of it, nothing moves.
		expect(splitCustomRules([actionRule(['x'], 'deny'), GET_REPOS]).denyMovesAhead).toBe(false);
	});

	it('a deny that also names a method or path stays with the path rules', () => {
		const deny: PermissionRule = { effect: 'deny', methods: ['GET'], operations: ['x'] };
		expect(splitCustomRules([deny]).pathRules).toEqual([deny]);
	});

	it('a Deny action beats an allowing path rule in the dry run', () => {
		const rules = draftRulesForPreset(
			'custom',
			joinCustomRules([{ effect: 'allow', path: '/repos', match_mode: 'prefix' }], {
				...none,
				deny: ['repos/delete'],
			}),
		);
		expect(
			explainRules(rules, {
				method: 'DELETE',
				path: '/repos/x',
				operation_id: 'repos/delete',
			}),
		).toEqual({ allowed: false, effect: 'deny', matched: true, ruleIndex: 0 });
		expect(
			explainRules(rules, { method: 'GET', path: '/repos/x', operation_id: 'repos/get' })
				.allowed,
		).toBe(true);
	});
});

describe('presetForRules — a leading Deny action rule', () => {
	it('is Custom, never mistaken for the preset after it', () => {
		expect(
			presetForRules([actionRule(['x'], 'deny'), { effect: 'allow', methods: ['GET'] }]),
		).toBe('custom');
	});
});

describe('presetForRules — saved rules reopen on the preset that made them', () => {
	it('recognises each fixed preset by its exact rules', () => {
		for (const preset of ['read', 'read-ask', 'all'] as const) {
			expect(presetForRules(rulesForPreset(preset, []) ?? [])).toBe(preset);
		}
	});

	it('reads the wire shapes the API returns — explicit regex, lowercase methods', () => {
		expect(presetForRules([{ effect: 'allow', path: '.*', match_mode: 'regex' }])).toBe('all');
		expect(
			presetForRules([
				{ effect: 'allow', methods: ['get'], path: null, operations: null },
				{ effect: 'require-approval', path: '.*', match_mode: 'regex' },
			]),
		).toBe('read-ask');
	});

	it('anything else is Custom; no rules is no choice yet', () => {
		expect(presetForRules([GET_REPOS])).toBe('custom');
		// Order matters (first match wins): reversed, it is not the preset.
		expect(
			presetForRules([
				{ effect: 'require-approval', path: '.*' },
				{ effect: 'allow', methods: ['GET'] },
			]),
		).toBe('custom');
		expect(presetForRules([{ effect: 'allow', path: '.*', match_mode: 'prefix' }])).toBe(
			'custom',
		);
		expect(presetForRules([])).toBeNull();
	});

	it('draftForRules carries the rules only for Custom', () => {
		expect(draftForRules([{ effect: 'allow', methods: ['GET'] }])).toEqual({
			preset: 'read',
			customRules: [],
		});
		expect(draftForRules([GET_REPOS])).toEqual({ preset: 'custom', customRules: [GET_REPOS] });
	});
});
