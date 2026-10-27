/**
 * AgentPicker (⌘K) — the archived group: folded, opened for an archived current
 * agent, revealed by a query, and part of the one arrow-key run.
 */
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import type { AgentEntity } from '@/modules/agents/api';
import { AgentPicker } from '@/modules/agents/components/flat/AgentPicker';

const agent = (
	id: string,
	name: string,
	status: AgentEntity['status'] = 'active',
): AgentEntity => ({
	id,
	name,
	description: null,
	status,
	ownerId: null,
	parentAgentId: null,
	denialReason: null,
	createdAt: '2026-01-01T00:00:00Z',
	approvedAt: null,
	attribution: { registeredBy: null, approvedBy: null, deniedBy: null },
	hasApiKey: false,
});

const FLEET: AgentEntity[] = [
	agent('w1', 'waiting-one', 'pending'),
	agent('alpha', 'alpha'),
	agent('bravo', 'bravo-billing'),
	agent('nope', 'denied-one', 'rejected'),
	agent('old1', 'old-billing', 'archived'),
	agent('old2', 'old-triage', 'archived'),
];

const API_COUNTS = new Map(FLEET.map((a, i) => [a.id, i]));

function renderPicker({
	selectedId = 'alpha',
	onSelect = vi.fn(),
	agents = FLEET,
}: {
	selectedId?: string | null;
	onSelect?: (id: string) => void;
	agents?: AgentEntity[];
} = {}) {
	function Harness() {
		const [open, setOpen] = useState(true);
		return (
			<AgentPicker
				open={open}
				onClose={() => setOpen(false)}
				agents={agents}
				selectedId={selectedId}
				onSelect={onSelect}
				apiCounts={API_COUNTS}
			/>
		);
	}
	return renderWithProviders(<Harness />);
}

const dialog = () => screen.getByRole('dialog', { name: 'Switch agent' });
const field = () => within(dialog()).getByRole('combobox', { name: 'Find an agent' });
const archivedToggle = () => within(dialog()).getByRole('button', { name: /Archived/ });
const rowIds = () =>
	within(dialog())
		.getAllByRole('option')
		.map((o) => o.getAttribute('data-agent-id'));

describe('AgentPicker — the archived group', () => {
	it('is folded by default, with its count beside the heading', async () => {
		renderPicker();
		await waitFor(() => expect(field()).toHaveFocus());
		expect(rowIds()).toEqual(['w1', 'alpha', 'bravo', 'nope']);
		const toggle = archivedToggle();
		expect(toggle).toHaveAttribute('aria-expanded', 'false');
		expect(toggle).toBeEnabled();
		expect(toggle).toHaveTextContent('2');
		expect(dialog()).toHaveTextContent('4 agents · 2 archived');
	});

	it('opens on a click, and its rows join the one arrow-key run', async () => {
		const user = userEvent.setup();
		const onSelect = vi.fn();
		renderPicker({ onSelect });
		await waitFor(() => expect(field()).toHaveFocus());

		await user.click(archivedToggle());
		expect(archivedToggle()).toHaveAttribute('aria-expanded', 'true');
		expect(rowIds()).toEqual(['w1', 'alpha', 'bravo', 'nope', 'old1', 'old2']);

		// The field keeps focus throughout; `aria-activedescendant` follows the highlight.
		const idOf = (id: string) =>
			within(dialog())
				.getAllByRole('option')
				.find((o) => o.getAttribute('data-agent-id') === id)!.id;
		field().focus();
		expect(field()).toHaveAttribute('aria-activedescendant', idOf('alpha'));
		await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');
		expect(field()).toHaveAttribute('aria-activedescendant', idOf('old1'));
		expect(within(dialog()).getAllByRole('option')[4]).toHaveAttribute('aria-selected', 'true');
		expect(field()).toHaveFocus();

		await user.keyboard('{Enter}');
		expect(onSelect).toHaveBeenCalledExactlyOnceWith('old1');
	});

	it('opens itself when the current agent is archived', async () => {
		renderPicker({ selectedId: 'old2' });
		await waitFor(() => expect(field()).toHaveFocus());
		// Otherwise the agent being looked at would be missing from its own picker.
		expect(archivedToggle()).toHaveAttribute('aria-expanded', 'true');
		expect(rowIds()).toContain('old2');
		const current = within(dialog()).getByText('(current)').closest('[role="option"]');
		expect(current).toHaveAttribute('data-agent-id', 'old2');
	});

	it('a query searches the archived agents too, and locks the toggle while it does', async () => {
		const user = userEvent.setup();
		renderPicker();
		await waitFor(() => expect(field()).toHaveFocus());

		await user.type(field(), 'billing');
		expect(rowIds()).toEqual(['bravo', 'old1']);
		expect(archivedToggle()).toHaveAttribute('aria-expanded', 'true');
		// So the fold cannot be closed over a revealed match while searching.
		expect(archivedToggle()).toBeDisabled();

		await user.clear(field());
		await waitFor(() => expect(archivedToggle()).toBeEnabled());
		expect(archivedToggle()).toHaveAttribute('aria-expanded', 'false');
		expect(rowIds()).toEqual(['w1', 'alpha', 'bravo', 'nope']);
	});

	it('an archived-only query leaves no "nothing matches" message', async () => {
		const user = userEvent.setup();
		renderPicker();
		await waitFor(() => expect(field()).toHaveFocus());
		await user.type(field(), 'triage');
		expect(rowIds()).toEqual(['old2']);
		expect(within(dialog()).queryByText(/No agents match/)).toBeNull();

		await user.clear(field());
		await user.type(field(), 'zzz');
		expect(within(dialog()).queryAllByRole('option')).toHaveLength(0);
		expect(within(dialog()).getByText(/No agents match “zzz”/)).toBeInTheDocument();
	});

	it('strikes through archived names, and leaves a rejected one standing', async () => {
		const user = userEvent.setup();
		renderPicker();
		await waitFor(() => expect(field()).toHaveFocus());
		await user.click(archivedToggle());
		const nameOf = (id: string) =>
			within(dialog())
				.getAllByRole('option')
				.find((o) => o.getAttribute('data-agent-id') === id)!
				.querySelector('[dir="auto"]');
		expect(nameOf('old1')).toHaveClass('line-through');
		expect(nameOf('nope')).not.toHaveClass('line-through');
		expect(nameOf('alpha')).not.toHaveClass('line-through');
	});

	it('shows no archived group at all when the fleet has none', async () => {
		renderPicker({ agents: FLEET.filter((a) => a.status !== 'archived') });
		await waitFor(() => expect(field()).toHaveFocus());
		expect(within(dialog()).queryByTestId('picker-archived')).toBeNull();
		expect(dialog()).toHaveTextContent('4 agents');
		expect(dialog()).not.toHaveTextContent('archived');
	});
});
