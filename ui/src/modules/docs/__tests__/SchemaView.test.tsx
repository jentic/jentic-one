import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SchemaView } from '@/modules/docs/components/SchemaView';
import type { OpenApiDocument } from '@/modules/docs/api/types';

/**
 * Regression coverage for the circular-`$ref`/`allOf` crash: recursive schemas
 * (trees, linked lists, mutually-referencing models) are valid OpenAPI and used
 * to overflow the stack in `flattenAllOf`, blanking the whole reference. These
 * assert the renderer terminates and produces output.
 */
describe('SchemaView circular schemas', () => {
	it('renders a self-referential allOf without hanging', () => {
		const spec: OpenApiDocument = {
			components: {
				schemas: {
					Node: {
						type: 'object',
						// A.allOf: [A] — a direct cycle.
						allOf: [{ $ref: '#/components/schemas/Node' }],
						properties: { id: { type: 'string' } },
					},
				},
			},
		};
		render(<SchemaView spec={spec} schema={{ $ref: '#/components/schemas/Node' }} />);
		expect(screen.getByText('id')).toBeInTheDocument();
	});

	it('renders a mutual allOf cycle (A→B→A) without hanging', () => {
		const spec: OpenApiDocument = {
			components: {
				schemas: {
					A: {
						type: 'object',
						allOf: [{ $ref: '#/components/schemas/B' }],
						properties: { a: { type: 'string' } },
					},
					B: {
						type: 'object',
						allOf: [{ $ref: '#/components/schemas/A' }],
						properties: { b: { type: 'string' } },
					},
				},
			},
		};
		render(<SchemaView spec={spec} schema={{ $ref: '#/components/schemas/A' }} />);
		// Both branches contribute their own property before the cycle is cut.
		expect(screen.getByText('a')).toBeInTheDocument();
		expect(screen.getByText('b')).toBeInTheDocument();
	});

	it('renders a recursive $ref (tree node with self-referential children)', () => {
		const spec: OpenApiDocument = {
			components: {
				schemas: {
					Tree: {
						type: 'object',
						properties: {
							value: { type: 'string' },
							children: {
								type: 'array',
								items: { $ref: '#/components/schemas/Tree' },
							},
						},
					},
				},
			},
		};
		render(<SchemaView spec={spec} schema={{ $ref: '#/components/schemas/Tree' }} />);
		expect(screen.getByText('Tree')).toBeInTheDocument();
	});

	it('renders a map-shaped schema (additionalProperties) as map<string, …>', () => {
		const spec: OpenApiDocument = { components: { schemas: {} } };
		render(
			<SchemaView
				spec={spec}
				schema={{ type: 'object', additionalProperties: { type: 'integer' } }}
			/>,
		);
		expect(screen.getByText(/map<string, integer>/)).toBeInTheDocument();
	});
});
