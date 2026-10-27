import { describe, expect, it } from 'vitest';
import { ruleChipWords } from '@/modules/agents/lib/ruleChip';
import type { ActionCatalog } from '@/shared/credentials/lib/actionRules';

const OPS = [
	{ operation_id: 'op_a', method: 'GET', path: '/v1/boardColumns', name: 'List board columns' },
	{ operation_id: 'op_b', method: 'POST', path: '/v1/boardColumns', name: 'Create a column' },
	{ operation_id: 'op_c', method: 'GET', path: '/v1/tags', name: 'List tags' },
	{ operation_id: 'op_d', method: 'DELETE', path: '/v1/tags/{id}', name: 'Delete a tag' },
	{ operation_id: 'op_e', method: 'GET', path: '/v1/ads', name: 'List ads' },
];
const ready: ActionCatalog = { status: 'ready', operations: OPS };
const loading: ActionCatalog = { status: 'loading', operations: [] };
const FIVE = OPS.map((o) => o.operation_id);

describe('ruleChipWords', () => {
	it('an action rule reads as its actions — never "Any", never "operations"', () => {
		const words = ruleChipWords(
			{ effect: 'allow', methods: null, path: null, operations: FIVE },
			ready,
		);
		expect(`${words.effect} ${words.scope}`).toBe('Allow 5 actions');
		expect(words.scope).not.toMatch(/Any|operation/);
		expect(words.details).toEqual([
			'GET /v1/ads · List ads',
			'GET /v1/boardColumns · List board columns',
			'POST /v1/boardColumns · Create a column',
			'GET /v1/tags · List tags',
			'DELETE /v1/tags/{id} · Delete a tag',
		]);
	});

	it('one action reads as the action itself', () => {
		const words = ruleChipWords({ effect: 'allow', operations: ['op_a'] }, ready);
		expect(words.scope).toBe('GET /v1/boardColumns');
		expect(words.details).toEqual(['GET /v1/boardColumns · List board columns']);
	});

	it('while the list loads, a count and a pending note — no ids', () => {
		const words = ruleChipWords({ effect: 'allow', operations: ['op_a', 'op_b'] }, loading);
		expect(words.scope).toBe('2 actions');
		expect(words.details).toEqual(["Reading this API's actions…"]);
	});

	it('Ask and Deny lead with their own word', () => {
		const ask = ruleChipWords({ effect: 'require-approval', operations: FIVE }, ready);
		expect([ask.effect, ask.effectKey, ask.scope]).toEqual(['Ask', 'ask', '5 actions']);
		const deny = ruleChipWords({ effect: 'deny', methods: ['DELETE'], path: '/v1/' }, ready);
		expect([deny.effect, deny.effectKey, deny.scope]).toEqual(['Deny', 'deny', 'DELETE /v1/']);
	});

	it('a path rule names its methods, or says "any method" when it has none', () => {
		expect(
			ruleChipWords({ effect: 'allow', methods: ['GET'], path: '/v1/x' }, ready).scope,
		).toBe('GET /v1/x');
		expect(ruleChipWords({ effect: 'allow', path: '/v1/x' }, ready).scope).toBe(
			'any method /v1/x',
		);
		expect(ruleChipWords({ effect: 'allow', methods: ['GET', 'POST'] }, ready).scope).toBe(
			'GET, POST',
		);
	});

	it('a rule narrowed by method and path as well as actions keeps both', () => {
		const words = ruleChipWords(
			{ effect: 'allow', methods: ['GET'], path: '/v1/boardColumns', operations: FIVE },
			ready,
		);
		expect(words.scope).toBe('GET /v1/boardColumns · 5 actions');
	});

	it('an effect it does not know is named as it is, not passed off as Allow', () => {
		expect(ruleChipWords({ effect: 'audit', path: '/x' }, ready).effect).toBe('Audit');
	});
});
