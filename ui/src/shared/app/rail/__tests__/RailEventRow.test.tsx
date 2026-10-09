import { describe, it, expect, vi } from 'vitest';
import { checkA11y, render, screen, userEvent, waitFor } from '@/__tests__/test-utils';
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
		groupKey: 'execution:execution.completed:',
	};
	return { ...base, ...partial };
}

describe('RailEventRow — action slot vs severity (issue #652)', () => {
	// Regression pin: action-required events can be emitted at INFO severity
	// (e.g. `agent.self_registered`). The row must not force them into the
	// compact 1-line layout that omits the inline links.
	it('renders the inline link for an INFO self-registration that requires action', () => {
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
		expect(screen.getByRole('button', { name: 'View agent' })).toBeInTheDocument();
		// Events are history: the rail never claims a decision is still pending.
		expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
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
		expect(screen.queryByRole('button', { name: 'View execution' })).not.toBeInTheDocument();
	});

	it("keeps an actionable row's text and time stamp at full contrast", async () => {
		const ev = makeEvent({
			type: 'agent.self_registered',
			kind: 'agent',
			requiresAction: true,
			tokens: { agent_id: 'agnt_1' },
			tsMs: Date.now() - 3_600_000,
		});
		const { container } = render(
			<div className="bg-surface-1">
				<RailEventRow ev={ev} />
			</div>,
		);
		const row = container.querySelector<HTMLElement>('[data-rail-row]')!;
		expect(getComputedStyle(row).opacity).toBe('1');
		expect(getComputedStyle(container.querySelector('time')!).opacity).toBe('1');
		await checkA11y(container);
	});
});

describe('RailEventRow — plain-language rows', () => {
	it('leads with the actor name, then the summary', () => {
		const ev = makeEvent({ title: 'Called GitHub · createIssue', actorId: 'agnt_1' });
		render(<RailEventRow ev={ev} actorName="invoice-bot" />);
		expect(screen.getByText('invoice-bot')).toBeInTheDocument();
		expect(screen.getByText(/Called GitHub · createIssue/)).toBeInTheDocument();
	});

	it("cuts a long actor name and shows it whole on hover; a click on it is the row's", async () => {
		const user = userEvent.setup();
		const long = `agent-${'x'.repeat(80)}`;
		const onToggleExpand = vi.fn();
		render(
			<div style={{ width: 320 }}>
				<RailEventRow
					ev={makeEvent({ title: 'Called GitHub', actorId: 'agnt_1' })}
					actorName={long}
					groupCount={2}
					onToggleExpand={onToggleExpand}
				/>
			</div>,
		);
		const name = screen.getByText(long);
		await waitFor(() => expect(name.scrollWidth).toBeGreaterThan(name.clientWidth));
		// Out of the tab order: the row's control already names the actor.
		expect(name).not.toHaveAttribute('tabindex');
		await user.hover(name);
		expect(await screen.findByRole('tooltip')).toHaveTextContent(long);
		await user.click(name);
		expect(onToggleExpand).toHaveBeenCalledTimes(1);
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
