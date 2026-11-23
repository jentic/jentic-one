/**
 * Action rules across the two ids an operation answers to — the registry id,
 * new with every revision, and the spec's `operationId`, which survives a
 * re-import — and the stale ids a re-import leaves behind.
 */
import { describe, expect, it } from 'vitest';
import {
	actionAliases,
	actionLine,
	actionsAtThisVersion,
	changedActionChoices,
	describeActions,
	storeActionChoices,
	twinActionIds,
	viewActionGroups,
	type ActionCatalog,
	type ActionOperation,
} from '@/shared/credentials/lib/actionRules';

const REG = (n: number): string => `op_${String(n).repeat(40).slice(0, 40)}`;
const CREATE = REG(1);
const LIST = REG(2);
const OLD_CREATE = REG(7);
const OLD_GONE = REG(8);

const OPS: ActionOperation[] = [
	{
		operation_id: CREATE,
		method: 'POST',
		path: '/orders',
		name: 'Create an order',
		spec_operation_id: 'createOrder',
		action_key: 'createOrder',
	},
	{
		operation_id: LIST,
		method: 'GET',
		path: '/orders',
		name: 'List orders',
		spec_operation_id: 'listOrders',
		action_key: 'listOrders',
	},
];

/** The current version, with what the earlier one's ids named. */
const catalog: ActionCatalog = {
	status: 'ready',
	operations: OPS,
	specIdsKnown: true,
	past: new Map([
		[OLD_CREATE, { method: 'POST', path: '/orders', summary: 'Create an order' }],
		[OLD_GONE, { method: 'DELETE', path: '/orders/{id}', summary: 'Cancel an order' }],
		['cancelOrder', { method: 'DELETE', path: '/orders/{id}', summary: 'Cancel an order' }],
	]),
};

describe('describeActions — both ids, and stale ones', () => {
	it('finds an action by its operationId as by its registry id', () => {
		const d = describeActions(['createOrder', LIST], catalog);
		expect(d).toMatchObject({ found: 2, missing: 0, unresolved: 0 });
		expect(d.entries.map(actionLine)).toEqual([
			'GET /orders · List orders',
			'POST /orders · Create an order',
		]);
	});

	it('names a stale id by what it was, and the current action at the same method and path', () => {
		const d = describeActions([OLD_CREATE, OLD_GONE], catalog);
		expect(d.missing).toBe(2);
		const [create, gone] = d.entries;
		expect(create).toMatchObject({
			kind: 'missing',
			was: { method: 'POST', path: '/orders' },
			now: { key: 'createOrder', method: 'POST', path: '/orders' },
		});
		expect(actionLine(create!)).toBe(
			'POST /orders · Create an order — from an earlier version',
		);
		// The current version has no such action: named, but nothing to move it to.
		expect(gone).toMatchObject({ kind: 'missing', was: { method: 'DELETE' } });
		expect(gone).not.toHaveProperty('now');
	});

	it('never calls an operationId gone before the spec ids are read', () => {
		const unread: ActionCatalog = { ...catalog, specIdsKnown: false };
		expect(describeActions(['someOtherApisId'], unread)).toMatchObject({
			missing: 0,
			unresolved: 1,
		});
		// A registry id is provably gone with the list whole.
		expect(describeActions([REG(9)], unread)).toMatchObject({ missing: 1 });
	});

	it('flags an operationId another covered API declares too', () => {
		const shared: ActionCatalog = {
			...catalog,
			operations: [{ ...OPS[0]!, action_key: CREATE, ambiguous_with: 'Orders v2' }, OPS[1]!],
		};
		const [entry] = describeActions(['createOrder'], shared).entries;
		expect(entry).toMatchObject({ kind: 'found', ambiguousWith: 'Orders v2' });
		// By its registry id it is this API's, unambiguously.
		expect(describeActions([CREATE], shared).entries[0]).not.toHaveProperty('ambiguousWith');
	});
});

describe('actionsAtThisVersion — the one-click update', () => {
	it('moves stale ids to the current action, keeps each effect, and counts what it cannot move', () => {
		const next = actionsAtThisVersion(
			{ allow: [LIST], ask: [OLD_GONE], deny: [OLD_CREATE] },
			catalog,
		);
		expect(next.groups).toEqual({
			allow: ['listOrders'],
			ask: [OLD_GONE],
			deny: ['createOrder'],
		});
		// LIST → listOrders (current id moved to its operationId), OLD_CREATE → createOrder.
		expect(next.changed).toBe(2);
		expect(next.left).toBe(1);
	});

	it('leaves ids it can read nothing about untouched', () => {
		const next = actionsAtThisVersion(
			{ allow: ['notOurs'], ask: [], deny: [] },
			{ ...catalog, specIdsKnown: false },
		);
		expect(next).toMatchObject({ groups: { allow: ['notOurs'] }, changed: 0, left: 0 });
	});
});

describe('By action choices across ids', () => {
	it('reads each action by its row key, whichever id holds it; stale ids are no row', () => {
		expect(
			viewActionGroups(
				{ allow: ['listOrders'], ask: [], deny: [CREATE, OLD_CREATE] },
				catalog,
			),
		).toEqual({ allow: [LIST], ask: [], deny: [CREATE, OLD_CREATE] });
	});

	it('a re-set writes the operationId and retires every other id that named the action', () => {
		const stored = { allow: [LIST], ask: [], deny: [OLD_CREATE] };
		const before = viewActionGroups(stored, catalog);
		// The operator sets Create an order to Deny in the list.
		const after = { ...before, deny: [...before.deny, CREATE] };
		const next = storeActionChoices(stored, before, after, catalog);
		// The untouched List orders keeps its registry id; the stale id is gone.
		expect(next).toEqual({ allow: [LIST], ask: [], deny: ['createOrder'] });
		// One action changed — the id it is held by — not two.
		expect(changedActionChoices(stored, next, catalog)).toBe(1);
	});

	it('knows every id that names an action', () => {
		expect([...(actionAliases(catalog).get(CREATE) ?? [])].sort()).toEqual(
			[CREATE, OLD_CREATE, 'createOrder'].sort(),
		);
	});
});

describe('an ambiguous action, named in every API that declares it', () => {
	const TWIN = REG(5);
	const shared: ActionCatalog = {
		...catalog,
		operations: [
			{
				...OPS[0]!,
				action_key: CREATE,
				action_keys: [CREATE, TWIN],
				ambiguous_with: 'Orders v2',
			},
			OPS[1]!,
		],
	};

	it('a choice writes the action’s registry id in each API', () => {
		const stored = { allow: [], ask: [], deny: [] };
		const before = viewActionGroups(stored, shared);
		const next = storeActionChoices(stored, before, { ...before, ask: [CREATE] }, shared);
		expect(next.ask).toEqual([CREATE, TWIN]);
		expect(twinActionIds(shared)).toEqual(new Set([TWIN]));
	});

	it('changing it moves every copy, so none is left behind in the old effect', () => {
		const stored = { allow: [], ask: [CREATE, TWIN], deny: [] };
		const before = viewActionGroups(stored, shared);
		const next = storeActionChoices(
			stored,
			before,
			{ ...before, ask: [], deny: [CREATE] },
			shared,
		);
		expect(next).toEqual({ allow: [], ask: [], deny: [CREATE, TWIN] });
	});

	it('the update moves a stale id to the action in each API, and counts only real moves', () => {
		const pastShared: ActionCatalog = { ...shared };
		const next = actionsAtThisVersion({ allow: [], ask: [OLD_CREATE], deny: [] }, pastShared);
		expect(next.groups.ask).toEqual([CREATE, TWIN]);
		expect(next.changed).toBe(1);
		// Already named in both: nothing to move.
		expect(
			actionsAtThisVersion({ allow: [], ask: [CREATE, TWIN], deny: [] }, shared).changed,
		).toBe(0);
	});
});
