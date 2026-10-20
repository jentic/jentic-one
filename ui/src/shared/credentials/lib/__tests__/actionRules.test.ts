import { describe, expect, it } from 'vitest';
import {
	actionCount,
	actionLine,
	actionTooltipLines,
	actionsNote,
	actionsSummary,
	describeActions,
	type ActionCatalog,
} from '@/shared/credentials/lib/actionRules';

const OPS = [
	{
		operation_id: 'op_list',
		method: 'get',
		path: '/v1/boardColumns',
		name: 'List board columns',
	},
	{
		operation_id: 'op_create',
		method: 'POST',
		path: '/v1/boardColumns',
		name: 'Create a column',
	},
	{ operation_id: 'op_tags', method: 'GET', path: '/v1/tags', name: null },
];

const ready: ActionCatalog = { status: 'ready', operations: OPS };

describe('describeActions', () => {
	it('names each found action by method, path and summary, sorted like the checklist', () => {
		const d = describeActions(['op_tags', 'op_create', 'op_list'], ready);
		expect(d).toMatchObject({ status: 'ready', count: 3, found: 3, missing: 0, unresolved: 0 });
		expect(d.entries.map(actionLine)).toEqual([
			'GET /v1/boardColumns · List board columns',
			'POST /v1/boardColumns · Create a column',
			'GET /v1/tags',
		]);
		expect(actionsNote(d)).toBeNull();
	});

	it('counts each id once', () => {
		expect(describeActions(['op_list', 'op_list'], ready).count).toBe(1);
	});

	it('while loading, names what the pages so far hold and leaves the rest pending', () => {
		const d = describeActions(['op_list', 'op_tags'], {
			status: 'loading',
			operations: OPS.slice(0, 1),
		});
		expect(d).toMatchObject({ status: 'loading', found: 1, missing: 0, unresolved: 1 });
		expect(actionsNote(d)).toBe("Reading this API's actions…");
		expect(actionsSummary(d)).toBe('2 actions');
	});

	it('with no list to read, nothing is named and nothing is called missing', () => {
		const d = describeActions(['op_list', 'op_tags'], {
			status: 'unavailable',
			operations: [],
		});
		expect(d).toMatchObject({ found: 0, missing: 0, unresolved: 2 });
		expect(actionsNote(d)).toMatch(/list of actions isn't available, so they can't be named/);
		expect(actionsSummary(d)).toBe('2 actions');
	});

	it('an id the complete list lacks is an action not in this version — its id is not the text', () => {
		const d = describeActions(['op_list', 'op_gone'], ready);
		expect(d).toMatchObject({ found: 1, missing: 1, unresolved: 0 });
		expect(d.entries[1]).toEqual({ kind: 'missing', id: 'op_gone' });
		expect(actionLine(d.entries[1])).toBe('An action not in this version of the API');
		expect(actionsNote(d)).toBe('One action is not in this version of the API.');
	});
});

describe('actionsSummary', () => {
	it('is the one action itself when there is exactly one, and known', () => {
		expect(actionsSummary(describeActions(['op_list'], ready))).toBe('GET /v1/boardColumns');
	});

	it('is a count otherwise', () => {
		expect(actionsSummary(describeActions(['op_list', 'op_tags'], ready))).toBe('2 actions');
		expect(actionsSummary(describeActions(['op_gone'], ready))).toBe('1 action');
		expect(actionCount(1200)).toBe('1,200 actions');
	});
});

describe('actionTooltipLines', () => {
	it('names up to the limit, then "+N more", never an id', () => {
		const many = Array.from({ length: 8 }, (_, i) => ({
			operation_id: `op_${i}`,
			method: 'GET',
			path: `/v1/items/${i}`,
			name: `Item ${i}`,
		}));
		const lines = actionTooltipLines(
			describeActions(
				many.map((o) => o.operation_id),
				{ status: 'ready', operations: many },
			),
			3,
		);
		expect(lines).toEqual([
			'GET /v1/items/0 · Item 0',
			'GET /v1/items/1 · Item 1',
			'GET /v1/items/2 · Item 2',
			'+5 more',
		]);
	});

	it('closes with the note when some actions cannot be named', () => {
		const lines = actionTooltipLines(describeActions(['op_list', 'op_gone'], ready));
		expect(lines).toEqual([
			'GET /v1/boardColumns · List board columns',
			'One action is not in this version of the API.',
		]);
		expect(lines.join(' ')).not.toContain('op_gone');
	});
});
