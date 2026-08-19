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
} from '@/modules/webhooks/api';

/** From `admin/services/webhooks/fanout.py` — never sent to an endpoint. */
const NEVER_RELAYED = ['credential.accessed', 'instance.booted', 'instance.initialized'];

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
});
