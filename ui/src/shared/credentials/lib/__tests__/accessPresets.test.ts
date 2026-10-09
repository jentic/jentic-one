/**
 * The access presets every bind surface offers, and the rules each one writes —
 * the workspace bind dialog and the Add-APIs queue both save exactly these.
 */
import { describe, it, expect } from 'vitest';
import {
	ALLOW_ALL_DESCRIPTION,
	coverageNote,
	draftRulesForPreset,
	presetOptions,
	rulesForPreset,
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
	it('Allow all says how far the credential reaches; the others do not change', () => {
		for (const reach of ['pinned', 'any-version', 'vendor-wide'] as const) {
			const [all, read, custom] = presetOptions(reach);
			expect(all).toMatchObject({ value: 'all', description: ALLOW_ALL_DESCRIPTION[reach] });
			expect(read).toMatchObject({ value: 'read', label: 'Read-only (GET only)' });
			expect(custom).toMatchObject({ value: 'custom', label: 'Custom rules' });
		}
	});

	it('only a credential reaching past one pinned API carries a coverage note', () => {
		expect(coverageNote('pinned')).toBeNull();
		expect(coverageNote('any-version')).toMatch(/future versions too/);
		expect(coverageNote('vendor-wide')).toMatch(/every API of its vendor/);
	});
});

describe('draftRulesForPreset + explainRules — the dry run of an unsaved choice', () => {
	const ask = (rules: PermissionRule[], method: string, path: string) =>
		explainRules(rules, { method, path, operation_id: null });

	it('Read-only: GET allowed by rule 0, POST default-denied', () => {
		const rules = draftRulesForPreset('read', []);
		expect(ask(rules, 'GET', '/repos/x')).toEqual({
			allowed: true,
			matched: true,
			ruleIndex: 0,
		});
		expect(ask(rules, 'POST', '/repos/x')).toEqual({
			allowed: false,
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
			matched: true,
			ruleIndex: 0,
		});
		expect(ask(rules, 'GET', '/repos/open')).toEqual({
			allowed: true,
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
