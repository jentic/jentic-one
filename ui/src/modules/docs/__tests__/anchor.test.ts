import { describe, it, expect } from 'vitest';
import {
	lookupKey,
	indexReference,
	operationAnchorId,
	modelAnchorId,
} from '@/modules/docs/lib/anchor';
import type { ReferenceEndpoint, ReferencePayload } from '@/modules/docs/api/types';

function endpoint(method: string, path: string): ReferenceEndpoint {
	return {
		method,
		path,
		surface: 'admin',
		summary: '',
		operation_id: null,
		authenticated: true,
		public: false,
		actor_types: [],
		required_scopes: ['x'],
		implied_scopes: {},
		auth_note: null,
		typical_caller: null,
		group: 'g',
	};
}

describe('lookupKey', () => {
	it('uppercases the method and ensures a leading slash', () => {
		expect(lookupKey('get', 'credentials')).toBe('GET /credentials');
		expect(lookupKey('GET', '/credentials')).toBe('GET /credentials');
	});
});

describe('indexReference', () => {
	const payload: ReferencePayload = {
		schema: 'jentic.endpoint-scope-tree/v1',
		total: 2,
		groups: ['g'],
		endpoints: [endpoint('GET', '/credentials/{credential_id}'), endpoint('POST', '/toolkits')],
	};
	const index = indexReference(payload);

	it('keys each endpoint by its canonical (method, path) lookup key', () => {
		expect(index.get('GET /credentials/{credential_id}')?.method).toBe('GET');
		expect(index.get('POST /toolkits')?.path).toBe('/toolkits');
		expect(index.size).toBe(2);
	});

	it('returns undefined for an unknown (method, path)', () => {
		expect(index.get('DELETE /nope')).toBeUndefined();
	});
});

describe('operationAnchorId / modelAnchorId uniqueness', () => {
	it('produces no duplicate ids across a realistic set of operations', () => {
		const ops: Array<[string, string]> = [
			['GET', '/credentials'],
			['POST', '/credentials'],
			['GET', '/credentials/{credential_id}'],
			['DELETE', '/credentials/{credential_id}'],
			['PUT', '/toolkits/{toolkit_id}/credentials/{credential_id}/permissions'],
			['GET', '/toolkits'],
			['POST', '/toolkits'],
		];
		const ids = ops.map(([m, p]) => operationAnchorId(m, p));
		expect(new Set(ids).size).toBe(ids.length);
	});

	it('keeps method in the id so same-path different-method operations differ', () => {
		expect(operationAnchorId('GET', '/credentials')).not.toBe(
			operationAnchorId('POST', '/credentials'),
		);
	});

	it('produces no duplicate model ids for distinct model names', () => {
		const names = [
			'Credential',
			'CredentialList',
			'Toolkit',
			'Toolkit_Binding',
			'AccessRequest',
		];
		const ids = names.map(modelAnchorId);
		expect(new Set(ids).size).toBe(ids.length);
	});
});
