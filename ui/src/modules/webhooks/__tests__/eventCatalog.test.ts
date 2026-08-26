/**
 * Pins the shape of the curated event catalog.
 *
 * The catalog is a hand-maintained mirror of the backend's relayable event set
 * (`EventType.ALL` minus `NEVER_RELAYED`). These specs don't re-check the copy —
 * they guard the structural invariants that would silently break the picker: no
 * duplicate types, every entry fully populated, and none of the three withheld
 * types leaking in (offering one would let a user subscribe to something the
 * platform can never deliver).
 */
import { describe, it, expect } from 'vitest';
import {
	WEBHOOK_EVENT_CATALOG,
	WEBHOOK_EVENT_BY_TYPE,
	WEBHOOK_EVENT_CATEGORY_LABELS,
	groupEventsByNoun,
	eventNoun,
} from '@/modules/webhooks/api';

/** From `admin/services/webhooks/fanout.py` — never sent to an endpoint. */
const NEVER_RELAYED = ['credential.accessed', 'instance.booted', 'instance.initialized'];

/**
 * The DRIFT PIN: the exact set of subscribable event types, sorted. This mirrors
 * the backend's `subscribable_event_catalog()` (`EventType.ALL` minus
 * `NEVER_RELAYED`). The **backend** side of this pin lives in
 * `tests/.../test_event_catalog.py`, which asserts the backend computes exactly
 * this list — so if a type is added, renamed, or newly withheld on the backend,
 * that test fails and this constant (and the curated catalog above it) must be
 * updated in lockstep. Together the two halves make silent drift impossible.
 */
const SUBSCRIBABLE_EVENT_TYPES = [
	'access_request.approved',
	'access_request.denied',
	'access_request.filed',
	'access_request.withdrawn',
	'agent.created',
	'agent.registration_approved',
	'agent.registration_denied',
	'agent.self_registered',
	'broker.pbac_denied',
	'broker.toolkit_binding_unserved',
	'catalog.update_available',
	'catalog.update_conflicts_overlay',
	'credential.bound_to_toolkit',
	'credential.connected',
	'credential.connection_failed',
	'credential.expired',
	'credential.expiring_soon',
	'credential.not_provisioned',
	'credential.refresh_failed',
	'credential.stored',
	'credential.unbound_from_toolkit',
	'credential.undecryptable',
	'execution.completed',
	'execution.failed',
	'execution.repeated_failure',
	'import.completed',
	'import.failed',
	'job.failed_permanently',
	'overlay.deprecated',
	'security.unauthorized_access_attempt',
	'toolkit.bound_to_agent',
	'toolkit.created',
	'toolkit.key_created',
	'toolkit.permission_rule_set',
	'toolkit.unbound_from_agent',
	'upstream.circuit_open',
];

describe('webhook event catalog', () => {
	it('has no duplicate event types', () => {
		const types = WEBHOOK_EVENT_CATALOG.map((e) => e.type);
		expect(new Set(types).size).toBe(types.length);
	});

	it('fully populates every entry', () => {
		for (const event of WEBHOOK_EVENT_CATALOG) {
			expect(event.type, `type on ${event.label}`).toMatch(/^[a-z_]+\.[a-z_]+$/);
			expect(event.label.length, `label on ${event.type}`).toBeGreaterThan(0);
			expect(event.description.length, `description on ${event.type}`).toBeGreaterThan(20);
			expect(WEBHOOK_EVENT_CATEGORY_LABELS[event.category]).toBeDefined();
		}
	});

	it('never offers a withheld (never-relayed) type', () => {
		for (const withheld of NEVER_RELAYED) {
			expect(WEBHOOK_EVENT_BY_TYPE.has(withheld)).toBe(false);
		}
	});

	it('indexes every entry by type', () => {
		expect(WEBHOOK_EVENT_BY_TYPE.size).toBe(WEBHOOK_EVENT_CATALOG.length);
		expect(WEBHOOK_EVENT_BY_TYPE.get('credential.expired')?.label).toBe('Credential expired');
	});

	it('matches the backend subscribable set exactly (drift pin)', () => {
		const frontend = WEBHOOK_EVENT_CATALOG.map((e) => e.type).sort();
		// The curated catalog must be exactly the backend's subscribable set — no
		// more (offering an undeliverable type), no fewer (hiding a real one).
		expect(frontend).toEqual([...SUBSCRIBABLE_EVENT_TYPES].sort());
		// And none of the withheld types may sneak in.
		for (const withheld of NEVER_RELAYED) {
			expect(SUBSCRIBABLE_EVENT_TYPES).not.toContain(withheld);
		}
	});

	it('groups events by noun prefix, preserving reading order', () => {
		const groups = groupEventsByNoun();
		// Every event lands in exactly one group, under its own noun (grouping
		// collects same-noun events together, so compare as a set, not by order).
		const flat = groups.flatMap((g) => g.events.map((e) => e.type));
		expect(flat.slice().sort()).toEqual(WEBHOOK_EVENT_CATALOG.map((e) => e.type).sort());
		expect(new Set(flat).size).toBe(flat.length);
		for (const group of groups) {
			for (const event of group.events) {
				expect(eventNoun(event.type)).toBe(group.noun);
			}
		}
		// The noun is the segment before the first dot.
		expect(eventNoun('credential.expired')).toBe('credential');
		expect(eventNoun('access_request.filed')).toBe('access_request');
	});
});
