import { describe, it, expect } from 'vitest';
import { parseSpec, refName, derefSchema } from '@/modules/docs/lib/apiSpec';
import { operationAnchorId } from '@/modules/docs/lib/anchor';
import type { OpenApiDocument } from '@/modules/docs/api/types';

const SPEC = {
	openapi: '3.1.0',
	info: {
		title: 'Test API',
		version: '9.9.9',
		description: 'Desc',
		summary: 'Tagline',
		contact: { name: 'Team', email: 'team@example.com', url: 'https://example.com' },
		license: { name: 'MIT', url: 'https://opensource.org/licenses/MIT' },
	},
	servers: [{ url: 'https://api.example.com', description: 'Prod' }, { url: '/' }],
	security: [{ bearerAuth: [] }],
	tags: [
		{ name: 'Credentials', description: 'Manage credentials' },
		{ name: 'System', description: 'Ops' },
		{ name: 'Loose' },
	],
	'x-tagGroups': [
		{ name: 'Core', tags: ['Credentials'] },
		{ name: 'Operations', tags: ['System'] },
		{ name: 'Empty', tags: ['NoOps'] },
	],
	paths: {
		'/credentials': {
			get: {
				tags: ['Credentials'],
				summary: 'List',
				operationId: 'listCreds',
				responses: { '200': { description: 'ok' } },
			},
			post: {
				tags: ['Credentials'],
				summary: 'Create',
				requestBody: {
					required: true,
					content: {
						'application/json': { schema: { $ref: '#/components/schemas/CredReq' } },
					},
				},
				responses: {
					'201': {
						description: 'created',
						content: {
							'application/json': { schema: { $ref: '#/components/schemas/Cred' } },
						},
					},
				},
			},
		},
		'/health': {
			get: {
				tags: ['System'],
				summary: 'Health',
				responses: { '200': { description: 'ok' } },
			},
		},
		'/loose': {
			get: {
				tags: ['Loose'],
				summary: 'Loose op',
				responses: { '200': { description: 'ok' } },
			},
		},
		'/untagged': {
			get: { summary: 'No tag', responses: { '200': { description: 'ok' } } },
		},
		'/params/{id}': {
			get: {
				tags: ['System'],
				summary: 'Params op',
				parameters: [
					{
						name: 'id',
						in: 'path',
						required: true,
						description: 'The resource id',
						schema: { type: 'string' },
					},
					{
						name: 'note',
						in: 'query',
						schema: { type: 'string', nullable: true },
					},
				],
				responses: { '200': { description: 'ok' } },
			},
		},
		'/multi': {
			post: {
				tags: ['System'],
				summary: 'Multi content',
				requestBody: {
					content: {
						'application/json': { schema: { type: 'object' } },
						'multipart/form-data': { schema: { type: 'object' } },
					},
				},
				responses: { '200': { description: 'ok' } },
			},
		},
	},
	components: {
		securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } },
		schemas: {
			Cred: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] },
			CredReq: { type: 'object', properties: { name: { type: 'string' } } },
		},
	},
} as unknown as OpenApiDocument;

describe('parseSpec', () => {
	const parsed = parseSpec(SPEC);

	it('reads info', () => {
		expect(parsed.title).toBe('Test API');
		expect(parsed.version).toBe('9.9.9');
	});

	it('orders groups by x-tagGroups and drops groups with no operations', () => {
		const names = parsed.groups.map((g) => g.name);
		// Core, Operations from x-tagGroups (Empty dropped — no ops), then a
		// trailing group for tags not placed by any group (Loose, Other).
		expect(names.slice(0, 2)).toEqual(['Core', 'Operations']);
		expect(names).not.toContain('Empty');
		expect(names[names.length - 1]).toBe('Other');
	});

	it('puts operations under their tag in document order', () => {
		const core = parsed.groups.find((g) => g.name === 'Core')!;
		const creds = core.tags.find((t) => t.name === 'Credentials')!;
		expect(creds.description).toBe('Manage credentials');
		expect(creds.operations.map((o) => `${o.method} ${o.path}`)).toEqual([
			'GET /credentials',
			'POST /credentials',
		]);
	});

	it('collects untagged ops under "Other"', () => {
		const other = parsed.groups.find((g) => g.name === 'Other')!;
		const tagNames = other.tags.map((t) => t.name);
		expect(tagNames).toContain('Loose');
		expect(tagNames).toContain('Other'); // the synthetic untagged bucket
	});

	it('resolves request/response bodies including content type', () => {
		const post = parsed.groups
			.flatMap((g) => g.tags)
			.flatMap((t) => t.operations)
			.find((o) => o.method === 'POST' && o.path === '/credentials')!;
		expect(post.requestRequired).toBe(true);
		expect(post.requestBodies[0].contentType).toBe('application/json');
		expect(refName(post.requestBodies[0].schema)).toBe('CredReq');
		expect(post.responses[0].status).toBe('201');
		expect(refName(post.responses[0].bodies[0].schema)).toBe('Cred');
	});

	it('inherits spec-level security when an op declares none', () => {
		const get = parsed.groups
			.flatMap((g) => g.tags)
			.flatMap((t) => t.operations)
			.find((o) => o.path === '/health')!;
		expect(get.security).toEqual(['bearerAuth']);
	});

	it('extracts and sorts component schemas as models', () => {
		expect(parsed.models.map((m) => m.name)).toEqual(['Cred', 'CredReq']);
	});

	it('exposes security schemes', () => {
		expect(parsed.securitySchemes.bearerAuth).toEqual({ type: 'http', scheme: 'bearer' });
	});

	it('reads parameters with description, required flag, and nullable constraint', () => {
		const op = parsed.groups
			.flatMap((g) => g.tags)
			.flatMap((t) => t.operations)
			.find((o) => o.path === '/params/{id}')!;
		const id = op.parameters.find((p) => p.name === 'id')!;
		expect(id.in).toBe('path');
		expect(id.required).toBe(true);
		expect(id.description).toBe('The resource id');
		expect(id.type).toBe('string');
		const note = op.parameters.find((p) => p.name === 'note')!;
		expect(note.required).toBe(false);
		expect(note.constraints).toContain('nullable');
	});

	it('keeps every request-body content type, not just the first', () => {
		const op = parsed.groups
			.flatMap((g) => g.tags)
			.flatMap((t) => t.operations)
			.find((o) => o.path === '/multi')!;
		expect(op.requestBodies.map((b) => b.contentType)).toEqual([
			'application/json',
			'multipart/form-data',
		]);
	});

	it('parses servers (base URLs) in order, dropping urlless entries', () => {
		expect(parsed.servers).toEqual([
			{ url: 'https://api.example.com', description: 'Prod' },
			{ url: '/', description: undefined },
		]);
	});

	it('parses info summary, contact, and license metadata', () => {
		expect(parsed.summary).toBe('Tagline');
		expect(parsed.meta.contactName).toBe('Team');
		expect(parsed.meta.contactEmail).toBe('team@example.com');
		expect(parsed.meta.contactUrl).toBe('https://example.com');
		expect(parsed.meta.licenseName).toBe('MIT');
		expect(parsed.meta.licenseUrl).toBe('https://opensource.org/licenses/MIT');
	});
});

describe('derefSchema', () => {
	it('resolves a $ref to its component schema and name', () => {
		const { schema, name } = derefSchema(SPEC, { $ref: '#/components/schemas/Cred' });
		expect(name).toBe('Cred');
		expect((schema as { type?: string }).type).toBe('object');
	});

	it('returns a non-ref node unchanged', () => {
		const node = { type: 'string' };
		expect(derefSchema(SPEC, node).schema).toBe(node);
	});
});

describe('operationAnchorId', () => {
	it('is stable, slug-safe, and uppercases the method', () => {
		expect(operationAnchorId('get', '/credentials/{id}')).toBe('op-GET--credentials-id-');
	});
});
