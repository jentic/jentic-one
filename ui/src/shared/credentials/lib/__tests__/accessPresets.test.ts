/**
 * The access presets every bind surface offers, and the rules each one writes —
 * the workspace bind dialog and the Add-APIs queue both save exactly these.
 */
import { describe, it, expect } from 'vitest';
import {
	ALLOW_ALL_DESCRIPTION,
	actionRule,
	coverageNote,
	draftRulesForPreset,
	isActionRule,
	joinCustomRules,
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
	it('three presets, least access first; Full access says how far the credential reaches', () => {
		for (const reach of ['pinned', 'any-version', 'vendor-wide'] as const) {
			const options = presetOptions(reach);
			expect(options.map((o) => o.value)).toEqual(['read', 'all', 'custom']);
			expect(options[1]).toMatchObject({
				label: 'Full access',
				description: ALLOW_ALL_DESCRIPTION[reach],
			});
			expect(options[2]).toMatchObject({
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
		expect(isActionRule({ ...ACTION, effect: 'deny' })).toBe(false);
		expect(isActionRule({ ...ACTION, operations: [] })).toBe(false);
		expect(isActionRule(GET_REPOS)).toBe(false);

		expect(splitCustomRules([GET_REPOS, ACTION])).toEqual({
			pathRules: [GET_REPOS],
			actionRules: [ACTION],
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

	it('re-joins with the action rules last, so the path rows keep their numbers', () => {
		expect(joinCustomRules([GET_REPOS], [ACTION])).toEqual([GET_REPOS, ACTION]);
		const rules = [GET_REPOS, ACTION, { effect: 'deny' as const, path: '/admin' }];
		const { pathRules, actionRules } = splitCustomRules(rules);
		expect(joinCustomRules(pathRules, actionRules)).toEqual([
			GET_REPOS,
			{ effect: 'deny', path: '/admin' },
			ACTION,
		]);
	});

	it('an action rule is never the "empty allow" that holds Custom incomplete', () => {
		expect(rulesForPreset('custom', joinCustomRules([GET_REPOS], [ACTION]))).toEqual([
			{ effect: 'allow', methods: ['GET'], path: '/repos' },
			{ effect: 'allow', operations: ['repos/get'] },
		]);
		expect(
			rulesForPreset('custom', joinCustomRules([{ effect: 'allow' }], [ACTION])),
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
