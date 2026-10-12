import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AgentBadge, AgentInitialsProvider, agentInitials } from '@/shared/ui/AgentBadge';

describe('agentInitials', () => {
	it('takes first+last word initials for multi-word names', () => {
		expect(agentInitials('inbox triage bot')).toBe('IB');
		expect(agentInitials('inbox-triage-bot')).toBe('IB');
		expect(agentInitials('support_agent')).toBe('SA');
	});

	it('takes the first two letters for single-word names', () => {
		expect(agentInitials('orchestrator')).toBe('OR');
	});

	it('returns empty string for missing names', () => {
		expect(agentInitials(undefined)).toBe('');
		expect(agentInitials('')).toBe('');
	});
});

describe('AgentBadge', () => {
	it('renders initials with an accessible label', () => {
		render(<AgentBadge id="agnt_1" name="support-agent" />);
		expect(screen.getByLabelText('Agent support-agent')).toHaveTextContent('SA');
	});

	it('is deterministic — same id keeps the same pastel tone', () => {
		const { container: a } = render(<AgentBadge id="agnt_1" name="a" />);
		const { container: b } = render(<AgentBadge id="agnt_1" name="b" />);
		// The tone is shared even though the names differ.
		const toneA = a.firstElementChild?.getAttribute('data-tone');
		expect(toneA).toMatch(/^\d$/);
		expect(b.firstElementChild?.getAttribute('data-tone')).toBe(toneA);
	});

	it('renders as a button and fires onClick when interactive', async () => {
		const onClick = vi.fn();
		const user = userEvent.setup();
		render(<AgentBadge id="agnt_1" name="support-agent" onClick={onClick} />);
		await user.click(screen.getByRole('button', { name: 'Agent support-agent' }));
		expect(onClick).toHaveBeenCalledOnce();
	});

	it('falls back to a generic label with no name', () => {
		render(<AgentBadge id="agnt_1" />);
		expect(screen.getByLabelText('Agent')).toBeInTheDocument();
	});
});

describe('AgentInitialsProvider', () => {
	const fleet = new Map([['agnt_1', 'SUP']]);

	it("gives a badge its fleet-unique initials by the agent's id", () => {
		render(
			<AgentInitialsProvider initials={fleet}>
				<AgentBadge id="agnt_1" name="support-agent" />
			</AgentInitialsProvider>,
		);
		expect(screen.getByLabelText('Agent support-agent')).toHaveTextContent('SUP');
	});

	it("falls back to the name's initials for an id it doesn't hold", () => {
		render(
			<AgentInitialsProvider initials={fleet}>
				<AgentBadge id="agnt_2" name="support-agent" />
			</AgentInitialsProvider>,
		);
		expect(screen.getByLabelText('Agent support-agent')).toHaveTextContent('SA');
	});

	it('yields to explicit initials on the badge', () => {
		render(
			<AgentInitialsProvider initials={fleet}>
				<AgentBadge id="agnt_1" name="support-agent" initials="XY" />
			</AgentInitialsProvider>,
		);
		expect(screen.getByLabelText('Agent support-agent')).toHaveTextContent('XY');
	});
});
