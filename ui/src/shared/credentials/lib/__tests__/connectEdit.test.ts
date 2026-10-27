import { describe, expect, it } from 'vitest';
import type {
	EditRuleView,
	ReviewEdit,
	SessionRevision,
} from '@/shared/credentials/api/vendors-types';
import {
	addedScopes,
	diffRules,
	revisionAddingRule,
	revisionChanges,
	revisionsAddingRules,
	revisionSummary,
	ruleChanges,
	rulesToConfirm,
} from '@/shared/credentials/lib/connectEdit';

const READ: EditRuleView = { id: 'apr_r', effect: 'allow', methods: ['GET'], path: '/v1/' };
const DENY: EditRuleView = { id: 'apr_d', effect: 'deny', methods: ['DELETE'], path: '/v1/' };
const SEND: EditRuleView = { id: null, effect: 'allow', methods: ['POST'], path: '/v1/send' };
const LABEL: EditRuleView = { id: null, effect: 'allow', methods: ['POST'], path: '/v1/labels' };

const EDIT: ReviewEdit = {
	binding: { agent_id: 'agnt_1', credential_id: 'cred_1', credential_name: 'Gmail' },
	revision: { id: 'csr_2', number: 2 },
	current: { rules: [READ, DENY], rule_set: null, suspended: false, granted_scopes: ['read'] },
	result: {
		target: 'binding',
		rules: [READ, SEND, LABEL],
		scopes: ['read', 'send'],
		suspended: false,
	},
	widens_access: true,
	other_bound_agents: [],
	needs_vendor_consent: true,
};

function revision(number: number, rules: EditRuleView[], scopes: string[]): SessionRevision {
	return {
		id: `csr_${number}`,
		number,
		created_by: 'agnt_1',
		created_at: '2026-10-19T10:00:00Z',
		proposal: {},
		result: { rules, scopes },
	};
}

describe('connectEdit', () => {
	it('diffs rules by content, flagging what widens', () => {
		expect(diffRules([READ, DENY], [READ, SEND]).map((c) => [c.kind, c.widens])).toEqual([
			['kept', false],
			['added', true],
			['removed', true],
		]);
	});

	it('gives the combined diff against today and the added scopes', () => {
		expect(ruleChanges(EDIT).map((c) => c.kind)).toEqual(['kept', 'added', 'added', 'removed']);
		expect(addedScopes(EDIT)).toEqual(['send']);
	});

	it("gives each revision's own change against the one before", () => {
		const [first, second] = revisionChanges(EDIT, [
			revision(1, [READ, SEND], ['read']),
			revision(2, [READ, SEND, LABEL], ['read', 'send']),
		]);
		expect(first.rules.map((c) => [c.kind, c.rule.path])).toEqual([
			['added', '/v1/send'],
			['removed', '/v1/'],
		]);
		expect(first.addedScopes).toEqual([]);
		expect(second.rules.map((c) => [c.kind, c.rule.path])).toEqual([['added', '/v1/labels']]);
		expect(second.addedScopes).toEqual(['send']);
	});

	it('sums up what each revision did, and which one added each rule', () => {
		const rebased = { ...revision(3, [READ, SEND, LABEL], ['read', 'send']), created_by: null };
		const replaced = {
			...revision(2, [READ, SEND, LABEL], ['read', 'send']),
			proposal: { rules: { set: [] }, resume: true },
		};
		const changes = revisionChanges(EDIT, [
			revision(1, [READ, SEND], ['read']),
			replaced,
			rebased,
		]);
		expect(changes.map(revisionSummary)).toEqual([
			'added 1 rule, removed 1 rule',
			'replaced the rule list, added 1 scope, asked to resume',
			'rebased onto the access as it is now',
		]);
		const introduced = revisionsAddingRules(changes);
		expect(revisionAddingRule(introduced, SEND)).toBe(1);
		expect(revisionAddingRule(introduced, LABEL)).toBe(2);
		expect(revisionAddingRule(introduced, READ)).toBeUndefined();
	});
});

describe('rulesToConfirm', () => {
	it('sends the rules as written, without the review-only id and added_by', () => {
		expect(rulesToConfirm([{ ...READ, comment: 'Read mail', added_by: 'agnt_1' }])).toEqual([
			{ effect: 'allow', methods: ['GET'], path: '/v1/', comment: 'Read mail' },
		]);
	});
});
