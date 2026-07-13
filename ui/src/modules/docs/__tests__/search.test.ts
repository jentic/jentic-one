import { describe, it, expect } from 'vitest';
import { buildSearchIndex, searchIndex } from '@/modules/docs/lib/search';
import { modelAnchorId, operationAnchorId } from '@/modules/docs/lib/anchor';
import type { ReferencePayload } from '@/modules/docs/api/types';

const reference: ReferencePayload = {
	schema: 'jentic.endpoint-scope-tree/v1',
	total: 1,
	groups: ['g'],
	endpoints: [
		{
			method: 'POST',
			path: '/toolkits',
			surface: 'admin',
			summary: 'Create toolkit',
			operation_id: 'createToolkit',
			authenticated: true,
			public: false,
			actor_types: [],
			required_scopes: ['x'],
			implied_scopes: {},
			auth_note: null,
			typical_caller: null,
			group: 'g',
		},
	],
};

describe('buildSearchIndex', () => {
	it('points endpoint hits at the exact operation anchor', () => {
		const index = buildSearchIndex(reference, undefined);
		const hit = searchIndex(index, 'POST /toolkits').find((i) => i.kind === 'endpoint');
		expect(hit?.anchor).toBe(operationAnchorId('POST', '/toolkits'));
	});

	it('indexes models and points them at the model anchor', () => {
		const index = buildSearchIndex(reference, undefined, ['ToolkitCreateResponse']);
		const hit = searchIndex(index, 'ToolkitCreate').find((i) => i.kind === 'model');
		expect(hit?.title).toBe('ToolkitCreateResponse');
		expect(hit?.anchor).toBe(modelAnchorId('ToolkitCreateResponse'));
	});

	it('omits models when none are supplied', () => {
		const index = buildSearchIndex(reference, undefined);
		expect(index.some((i) => i.kind === 'model')).toBe(false);
	});
});

describe('anchor helpers', () => {
	it('modelAnchorId is slug-safe', () => {
		expect(modelAnchorId('Foo.Bar/Baz')).toBe('model-Foo-Bar-Baz');
	});
});
