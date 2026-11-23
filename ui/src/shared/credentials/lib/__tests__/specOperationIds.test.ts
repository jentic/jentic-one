import { describe, it, expect } from 'vitest';
import {
	actionAt,
	registryOperationId,
	specActions,
	storedOperationIds,
} from '@/shared/credentials/lib/specOperationIds';

const SPEC = {
	paths: {
		'/orders': {
			get: { operationId: 'listOrders', summary: 'List orders' },
			post: { operationId: 'createOrder', summary: ' Create an order ' },
			parameters: [{ name: 'x', in: 'query' }],
		},
		'/orders/{id}': {
			get: { operationId: 'getOrder' },
			delete: {},
			patch: { operationId: '   ' },
		},
		'/dupe/a': { get: { operationId: 'dupe' } },
		'/dupe/b': { get: { operationId: 'dupe' } },
		'/long': { get: { operationId: 'x'.repeat(256) } },
	},
};

describe('specActions', () => {
	it('lists every declared operation, method upper-cased, ignoring non-method keys', () => {
		const actions = specActions(SPEC);
		expect(actions.map((a) => actionAt(a.method, a.path))).toEqual([
			'GET /orders',
			'POST /orders',
			'GET /orders/{id}',
			'DELETE /orders/{id}',
			'PATCH /orders/{id}',
			'GET /dupe/a',
			'GET /dupe/b',
			'GET /long',
		]);
		expect(actions[1]).toMatchObject({
			operationId: 'createOrder',
			summary: 'Create an order',
		});
		expect(actions[3]).toMatchObject({ operationId: null, summary: null });
	});

	it('reads nothing from a document without paths', () => {
		expect(specActions(null)).toEqual([]);
		expect(specActions({ openapi: '3.0.0' })).toEqual([]);
	});
});

describe('storedOperationIds', () => {
	it('keeps only ids the registry stores unchanged: declared, not blank, not too long, used once', () => {
		const ids = storedOperationIds(specActions(SPEC));
		expect(Object.fromEntries(ids)).toEqual({
			'GET /orders': 'listOrders',
			'POST /orders': 'createOrder',
			'GET /orders/{id}': 'getOrder',
		});
	});
});

describe('registryOperationId', () => {
	it('recomputes the registry id from revision, path and method, as the registry does', async () => {
		// A row of a real registry: `op_` + sha256("<revision>:<path>:<METHOD>")[:40].
		expect(
			await registryOperationId('af95cb22-0b4c-4f26-9fcb-7e05b8169d0f', '/candidates', 'get'),
		).toBe('op_189a264dfb2a430b28983a276ba601243cbc8bc6');
		expect(
			await registryOperationId(
				'af95cb22-0b4c-4f26-9fcb-7e05b8169d0f',
				'/candidates',
				'POST',
			),
		).toBe('op_dd412852dbb027a21010019a141611d438e92438');
	});
});
