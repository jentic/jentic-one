import { describe, it, expect } from 'vitest';
import { diffSpecs } from '@/modules/workspace/api/specDiff';

const baseSpec = {
	openapi: '3.0.4',
	info: { title: 'Petstore', version: '1.0.27' },
	servers: [{ url: 'https://us.petstore.demo/api/v3' }],
	paths: {
		'/pet': { put: { summary: 'Update an existing pet' } },
		'/store/order': { post: { summary: 'Place an order' } },
	},
};

describe('diffSpecs', () => {
	it('returns no entries for identical documents', () => {
		const { entries, truncated } = diffSpecs(baseSpec, structuredClone(baseSpec));
		expect(entries).toEqual([]);
		expect(truncated).toBe(false);
	});

	it('emits one changed entry per changed subtree, not per document', () => {
		const target = structuredClone(baseSpec);
		target.servers = [
			{ url: 'https://eu.petstore.demo/api/v3' },
			{ url: 'https://us.petstore.demo/api/v3' },
		];
		const { entries } = diffSpecs(baseSpec, target);
		expect(entries).toEqual([
			{
				path: '$.servers',
				kind: 'changed',
				before: baseSpec.servers,
				after: target.servers,
			},
		]);
	});

	it('recurses into objects so sibling keys stay out of the diff', () => {
		const target = structuredClone(baseSpec);
		target.info.version = '1.0.28';
		const { entries } = diffSpecs(baseSpec, target);
		expect(entries).toEqual([
			{ path: '$.info.version', kind: 'changed', before: '1.0.27', after: '1.0.28' },
		]);
	});

	it('reports added and removed subtrees, bracket-quoting path keys', () => {
		const target = structuredClone(baseSpec) as Record<string, unknown>;
		const paths = target.paths as Record<string, unknown>;
		delete paths['/store/order'];
		paths['/user'] = { post: { summary: 'Create user' } };
		const { entries } = diffSpecs(baseSpec, target);
		expect(entries).toEqual([
			{
				path: "$.paths['/store/order']",
				kind: 'removed',
				before: baseSpec.paths['/store/order'],
			},
			{
				path: "$.paths['/user']",
				kind: 'added',
				after: { post: { summary: 'Create user' } },
			},
		]);
	});

	it('treats non-object roots as a single changed leaf', () => {
		const { entries } = diffSpecs('a', 'b');
		expect(entries).toEqual([{ path: '$', kind: 'changed', before: 'a', after: 'b' }]);
	});

	it('caps the entry list and flags truncation', () => {
		const before: Record<string, number> = {};
		const after: Record<string, number> = {};
		for (let i = 0; i < 50; i += 1) {
			before[`k${i}`] = i;
			after[`k${i}`] = i + 1;
		}
		const { entries, truncated } = diffSpecs(before, after, { maxEntries: 10 });
		expect(entries).toHaveLength(10);
		expect(truncated).toBe(true);
	});

	it('handles keys that shadow Object.prototype members', () => {
		// Schema property names like `constructor`/`toString` are legal in
		// OpenAPI documents; a prototype-chain `in` check would silently drop
		// their removal and mis-report their addition as `changed`.
		const base = JSON.parse('{"schema":{"constructor":{"type":"string"}}}') as unknown;
		const target = JSON.parse('{"schema":{}}') as unknown;
		expect(diffSpecs(base, target).entries).toEqual([
			{ path: '$.schema.constructor', kind: 'removed', before: { type: 'string' } },
		]);
		expect(diffSpecs(target, base).entries).toEqual([
			{ path: '$.schema.constructor', kind: 'added', after: { type: 'string' } },
		]);
	});
});
