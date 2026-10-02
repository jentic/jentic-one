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
	// Regression pin: action-required events can be emitted at INFO severity
	// (e.g. `agent.self_registered`). The row must not force INFO events into a
	// compact 1-line layout that omits the action slot — the Review/Acknowledge
	// actions would never appear with real data. An event that requires action
	// must render its actions regardless of severity.
	it('renders Review/Acknowledge for an INFO self-registration that requires action', () => {
		const ev = makeEvent({
			id: 'evt_selfreg',
			type: 'agent.self_registered',
			kind: 'agent',
			severity: 'info',
			title: 'Agent self-registered: invoice-bot',
			requiresAction: true,
			tokens: { agent_id: 'agnt_1' },
		});
		render(<RailEventRow ev={ev} onAction={() => {}} />);
		expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
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
		expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument();
	});

	it('collapses an acknowledged action-required event to compact (no buttons)', () => {
		const ev = makeEvent({
			id: 'evt_acked',
			type: 'agent.self_registered',
			kind: 'agent',
			severity: 'info',
			title: 'Agent self-registered: invoice-bot',
			requiresAction: true,
			acknowledged: true,
			tokens: { agent_id: 'agnt_1' },
		});
		const { container } = render(<RailEventRow ev={ev} onAction={() => {}} />);
		expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
		// Handled rows just dim — no "Acked" label competing with the summary.
		expect(screen.queryByText('Acked')).not.toBeInTheDocument();
		expect(container.querySelector('[data-rail-row]')).toHaveClass('opacity-55');
	});
});

describe('RailEventRow — plain-language rows', () => {
	it('leads with the actor name, then the summary', () => {
		const ev = makeEvent({ title: 'Called GitHub · createIssue', actorId: 'agnt_1' });
		render(<RailEventRow ev={ev} actorName="invoice-bot" />);
		expect(screen.getByText('invoice-bot')).toBeInTheDocument();
		expect(screen.getByText(/Called GitHub · createIssue/)).toBeInTheDocument();
	});

	it('shows just the summary when the actor is unknown (no jargon prefix)', () => {
		const ev = makeEvent({
			title: 'Import finished',
			type: 'import.completed',
			kind: 'import',
		});
		const { container } = render(<RailEventRow ev={ev} />);
		expect(container).toHaveTextContent(/^Import finished/);
	});

	it('only colours failures: info rows carry no stripe, errors a red one', () => {
		const { container, rerender } = render(<RailEventRow ev={makeEvent({})} />);
		expect(container.querySelector('[data-rail-row]')).toHaveClass('border-l-transparent');
		rerender(<RailEventRow ev={makeEvent({ severity: 'error', title: 'boom' })} />);
		expect(container.querySelector('[data-rail-row]')).toHaveClass('border-l-danger');
	});
});
