import { describe, it, expect } from 'vitest';
import { parseSpecOperations, opDetailKey } from '@/modules/workspace/api/specOperations';

describe('parseSpecOperations', () => {
	it('returns empty structures for a non-object / partial spec', () => {
		for (const input of [null, undefined, 42, 'nope', {}]) {
			const parsed = parseSpecOperations(input);
			expect(parsed.operations.size).toBe(0);
			expect(parsed.securitySchemes).toEqual({});
		}
	});

	it('extracts security schemes from components', () => {
		const parsed = parseSpecOperations({
			components: {
				securitySchemes: {
					bearerAuth: { type: 'http', scheme: 'bearer' },
					// Non-object values are ignored defensively.
					bogus: 'not-an-object',
				},
			},
		});
		expect(parsed.securitySchemes.bearerAuth).toEqual({ type: 'http', scheme: 'bearer' });
		expect(parsed.securitySchemes.bogus).toBeUndefined();
	});

	it('merges path-level params into each operation and keys by METHOD path', () => {
		const parsed = parseSpecOperations({
			paths: {
				'/things/{id}': {
					parameters: [{ name: 'id', in: 'path', required: true }],
					get: {
						parameters: [{ name: 'expand', in: 'query', description: 'Expand fields' }],
					},
				},
			},
		});
		const detail = parsed.operations.get(opDetailKey('get', '/things/{id}'));
		expect(detail).toBeDefined();
		expect(detail?.parameters.map((p) => p.name)).toEqual(['id', 'expand']);
		// Path param keeps required; query param defaults to not required.
		expect(detail?.parameters[0]).toMatchObject({ name: 'id', in: 'path', required: true });
		expect(detail?.parameters[1]).toMatchObject({
			name: 'expand',
			in: 'query',
			required: false,
			description: 'Expand fields',
		});
	});

	it('falls back to document-level security when the operation omits it', () => {
		const parsed = parseSpecOperations({
			security: [{ bearerAuth: [] }],
			paths: {
				'/inherits': { get: {} },
				'/overrides': { get: { security: [{ apiKey: [] }] } },
				'/none': { get: { security: [] } },
			},
		});
		expect(parsed.operations.get(opDetailKey('get', '/inherits'))?.security).toEqual([
			'bearerAuth',
		]);
		expect(parsed.operations.get(opDetailKey('get', '/overrides'))?.security).toEqual([
			'apiKey',
		]);
		// An explicit empty array opts out of the default (not the fallback).
		expect(parsed.operations.get(opDetailKey('get', '/none'))?.security).toEqual([]);
	});

	it('ignores non-HTTP-method keys on a path item', () => {
		const parsed = parseSpecOperations({
			paths: {
				'/x': {
					summary: 'not an operation',
					parameters: [],
					get: {},
				},
			},
		});
		expect(parsed.operations.size).toBe(1);
		expect(parsed.operations.has(opDetailKey('get', '/x'))).toBe(true);
	});
});
