import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import { adaptEvent, kindForType } from '@/shared/lib/agentStream';
import type { EventResponse } from '@/shared/api';
import type { StreamEvent } from '@/shared/lib/agentStream';

function makeEvent(partial: Partial<StreamEvent>): StreamEvent {
	const base: StreamEvent = {
		id: 'ev_catalog',
		tsMs: Date.now(),
		type: 'catalog.update_available',
		kind: 'catalog',
		severity: 'warning',
		title: 'Update available for stripe.com',
		tokens: {
			api_id: 'stripe.com',
			vendor: 'stripe.com',
			name: 'stripe-api',
			version: '1',
		},
		links: {},
		requiresAction: true,
		acknowledged: false,
		groupKey: 'catalog:catalog.update_available:',
	};
	return { ...base, ...partial };
}

describe('catalog/overlay stream kind (L5)', () => {
	it('maps catalog.* and overlay.* to the catalog kind (not "other")', () => {
		expect(kindForType('catalog.update_available')).toBe('catalog');
		expect(kindForType('catalog.update_conflicts_overlay')).toBe('catalog');
		expect(kindForType('overlay.deprecated')).toBe('catalog');
		expect(kindForType('mystery.thing')).toBe('other');
	});

	it('adapts a catalog event carrying the API triple + api_id into tokens', () => {
		const wire = {
			event_id: 'ev_1',
			type: 'catalog.update_available',
			severity: 'warning',
			summary: 'Update available',
			created_at: '2026-01-01T00:00:00Z',
			requires_action: true,
			acknowledged: false,
			data: {
				api_id: 'stripe.com',
				vendor: 'stripe.com',
				name: 'stripe-api',
				version: '1',
				spec_url: 'https://example/spec.json',
				event_class: 'catalog',
			},
		} as unknown as EventResponse;

		const ev = adaptEvent(wire);
		expect(ev.kind).toBe('catalog');
		expect(ev.tokens.api_id).toBe('stripe.com');
		expect(ev.tokens.vendor).toBe('stripe.com');
		expect(ev.tokens.version).toBe('1');
	});

	it('renders a Review action for a catalog.update_available event', () => {
		render(<RailEventRow ev={makeEvent({})} onAction={() => {}} />);
		expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
	});

	it('surfaces the conflict "why" hint for a catalog.update_conflicts_overlay event', () => {
		const wire = {
			event_id: 'ev_conflict',
			type: 'catalog.update_conflicts_overlay',
			severity: 'warning',
			summary: 'Update conflicts with overlay',
			created_at: '2026-01-01T00:00:00Z',
			requires_action: true,
			acknowledged: false,
			data: {
				api_id: 'stripe.com',
				vendor: 'stripe.com',
				name: 'stripe-api',
				version: '1',
				overlay_id: 'ovl_1',
				conflict: {
					base_digest: 'basedigest0000abcdef',
					served_digest: 'serveddigest111abcdef',
					upstream_digest: 'upstreamdigest22abcdef',
				},
			},
		} as unknown as EventResponse;

		const ev = adaptEvent(wire);
		render(<RailEventRow ev={ev} onAction={() => {}} />);
		expect(
			screen.getByText(/Upstream moved off the base your overlay was built on/),
		).toBeInTheDocument();
		// Short 12-char digest prefix is shown, not the full digest.
		expect(screen.getByText(/basedigest00/)).toBeInTheDocument();
	});
});
