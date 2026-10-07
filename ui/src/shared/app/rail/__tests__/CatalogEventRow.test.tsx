import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import {
	adaptEvent,
	inlineActionsFor,
	kindForType,
	primaryDestinationFor,
} from '@/shared/lib/agentStream';
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
		resolved: false,
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
	});

	it('surfaces the conflict "why" hint for a catalog.update_conflicts_overlay event', () => {
		const wire = {
			event_id: 'ev_conflict',
			type: 'catalog.update_conflicts_overlay',
			severity: 'warning',
			summary: 'Update conflicts with overlay',
			created_at: '2026-01-01T00:00:00Z',
			requires_action: true,
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

	describe('hub deep-links', () => {
		const HUB = '/library/workspace/stripe.com/stripe-api/1';
		const reviewHref = (ev: StreamEvent) => {
			const action = inlineActionsFor(ev).find((a) => a.kind === 'view_api');
			return action?.href?.(ev);
		};

		it('sends a plain update-available event to the Overview (Re-import lives there)', () => {
			const ev = makeEvent({});
			expect(primaryDestinationFor(ev)).toBe(HUB);
			expect(reviewHref(ev)).toBe(HUB);
		});

		it.each(['catalog.update_conflicts_overlay', 'overlay.deprecated'])(
			'sends %s to the Versions tab (where overlays live)',
			(type) => {
				const ev = makeEvent({ type });
				expect(primaryDestinationFor(ev)).toBe(`${HUB}?tab=versions`);
				if (type === 'catalog.update_conflicts_overlay') {
					expect(reviewHref(ev)).toBe(`${HUB}?tab=versions`);
				}
			},
		);
	});
});
