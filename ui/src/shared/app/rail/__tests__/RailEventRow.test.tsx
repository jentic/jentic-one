import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import type { StreamEvent } from '@/shared/lib/agentStream';

function makeEvent(partial: Partial<StreamEvent>): StreamEvent {
	const base: StreamEvent = {
		id: 'ev_test',
		tsMs: Date.now(),
		type: 'execution.completed',
		kind: 'execution',
		severity: 'info',
		title: 'test event',
		tokens: {},
		links: {},
		requiresAction: false,
		acknowledged: false,
		groupKey: 'execution:execution.completed:',
	};
	return { ...base, ...partial };
}

describe('RailEventRow — action slot vs severity (issue #652)', () => {
	// Regression: real `access_request.filed` events are emitted at INFO
	// severity. The row previously forced INFO events into a compact 1-line
	// layout that omits the action slot, so View/Deny never appeared with real
	// data (they only showed under MSW, which seeded `warning`). An event that
	// requires a decision must render its actions regardless of severity.
	it('renders View/Deny for an INFO filed access request that requires action', () => {
		const ev = makeEvent({
			id: 'evt_filed',
			type: 'access_request.filed',
			kind: 'access_request',
			severity: 'info',
			title: 'Access request filed: github read',
			requiresAction: true,
			tokens: { access_request_id: 'ar_1' },
		});
		render(<RailEventRow ev={ev} onAction={() => {}} onOpenRequest={() => {}} />);
		expect(screen.getByRole('button', { name: 'View' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Deny' })).toBeInTheDocument();
	});

	it('keeps a plain INFO event compact (no action slot) when it does not require action', () => {
		const ev = makeEvent({
			id: 'evt_info',
			type: 'execution.completed',
			kind: 'execution',
			severity: 'info',
			title: 'Execution completed',
			requiresAction: false,
		});
		render(<RailEventRow ev={ev} onAction={() => {}} />);
		expect(screen.queryByRole('button', { name: 'View' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument();
	});

	it('collapses an acknowledged action-required event to compact (no buttons)', () => {
		const ev = makeEvent({
			id: 'evt_acked',
			type: 'access_request.filed',
			kind: 'access_request',
			severity: 'info',
			title: 'Access request filed: github read',
			requiresAction: true,
			acknowledged: true,
			tokens: { access_request_id: 'ar_1' },
		});
		render(<RailEventRow ev={ev} onAction={() => {}} onOpenRequest={() => {}} />);
		expect(screen.queryByRole('button', { name: 'View' })).not.toBeInTheDocument();
		expect(screen.getByText('Acked')).toBeInTheDocument();
	});
});
