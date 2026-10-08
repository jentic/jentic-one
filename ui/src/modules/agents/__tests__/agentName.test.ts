import { describe, it, expect } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
	FIRST_AGENT_NAME,
	NEXT_AGENT_NAME,
	duplicateAgentName,
	duplicateAgentNameHint,
	suggestAgentName,
} from '@/modules/agents/lib/agentName';
import { useRegisterName, type RegisterNameOptions } from '@/modules/agents/lib/useRegisterName';

describe('suggestAgentName', () => {
	it('suggests my-first-agent for an empty org and my-agent over any roster', () => {
		expect(suggestAgentName([])).toBe(FIRST_AGENT_NAME);
		expect(suggestAgentName(['inbox-triage-bot'])).toBe(NEXT_AGENT_NAME);
	});

	it('skips a taken base, whatever status its owner has, case- and space-insensitively', () => {
		// The names come from the whole roster: an archived "my-first-agent"
		// still holds the name.
		expect(suggestAgentName(['My-First-Agent '], FIRST_AGENT_NAME)).toBe('my-first-agent-2');
		expect(suggestAgentName(['MY-AGENT'])).toBe('my-agent-2');
	});

	it('numbers on from -2 to the first free suffix', () => {
		expect(suggestAgentName(['my-agent', 'my-agent-2'])).toBe('my-agent-3');
		expect(suggestAgentName(['my-agent', 'my-agent-3'])).toBe('my-agent-2');
		expect(
			suggestAgentName(
				['my-first-agent', 'my-first-agent', 'my-first-agent-2', 'my-first-agent-3'],
				FIRST_AGENT_NAME,
			),
		).toBe('my-first-agent-4');
	});

	it('keeps a free base as it is', () => {
		expect(suggestAgentName(['alpha-2', 'gamma-bot'], FIRST_AGENT_NAME)).toBe(FIRST_AGENT_NAME);
	});
});

describe('duplicateAgentName', () => {
	it('returns the existing name a typed one matches, trimmed and case-insensitively', () => {
		expect(duplicateAgentName(['gamma-bot', 'wmq'], '  Gamma-Bot ')).toBe('gamma-bot');
		expect(duplicateAgentName(['gamma-bot'], 'gamma-bot-2')).toBeNull();
		expect(duplicateAgentName(['gamma-bot'], '   ')).toBeNull();
	});

	it('words the hint around the existing name', () => {
		expect(duplicateAgentNameHint('wmq')).toBe(
			'An agent named wmq already exists — pick a different name so you can tell them apart.',
		);
	});
});

describe('useRegisterName', () => {
	const render = (initialProps: RegisterNameOptions) =>
		renderHook((props: RegisterNameOptions) => useRegisterName(props), { initialProps });

	it('follows the roster while it loads, then settles', () => {
		const { result, rerender } = render({ names: [], rosterRead: false });
		expect(result.current.name).toBe('my-first-agent');

		// The first page lands after the first render: the suggestion moves with it.
		rerender({ names: ['my-agent'], rosterRead: false });
		expect(result.current.name).toBe('my-agent-2');
		rerender({ names: ['my-agent', 'my-agent-2'], rosterRead: true });
		expect(result.current.name).toBe('my-agent-3');
		expect(result.current.commandName).toBe('my-agent-3');

		// Settled: an arrival carrying the suggestion must not move the command.
		rerender({ names: ['my-agent', 'my-agent-2', 'my-agent-3'], rosterRead: true });
		expect(result.current.name).toBe('my-agent-3');
	});

	it('never overwrites a typed name when the roster loads', () => {
		const { result, rerender } = render({ names: [], rosterRead: false });
		act(() => result.current.setName('research-bot'));
		rerender({ names: ['gamma-bot', 'my-agent'], rosterRead: true });
		expect(result.current.name).toBe('research-bot');

		// A cleared field is the operator's too; the command falls back to the suggestion.
		act(() => result.current.setName(''));
		rerender({ names: ['gamma-bot', 'my-agent', 'x'], rosterRead: true });
		expect(result.current.name).toBe('');
		expect(result.current.commandName).toBe('my-agent-2');
	});

	it('flags a typed name another agent has, and clears the flag once it differs', () => {
		const { result } = render({ names: ['gamma-bot', 'wmq'], rosterRead: true });
		expect(result.current.duplicateOf).toBeNull();
		act(() => result.current.setName('WMQ'));
		expect(result.current.duplicateOf).toBe('wmq');
		act(() => result.current.setName('wmq-2'));
		expect(result.current.duplicateOf).toBeNull();
	});

	it('back to listening after an arrival, an untouched suggestion skips the arrival’s name', () => {
		const { result, rerender } = render({ names: ['wmq'], rosterRead: true });
		expect(result.current.name).toBe('my-agent');
		// The arrival carries the suggestion; the command is off screen.
		rerender({ names: ['wmq', 'my-agent'], rosterRead: true, listening: false });
		expect(result.current.commandName).toBe('my-agent');
		// Denied: listening again, and "my-agent" is the denied agent's.
		rerender({ names: ['wmq', 'my-agent'], rosterRead: true, listening: true });
		expect(result.current.name).toBe('my-agent-2');
	});

	it('reset drops the typed name and re-suggests from the current roster', () => {
		const { result, rerender } = render({ names: ['my-agent'], rosterRead: true });
		act(() => result.current.setName('research-bot'));
		rerender({ names: ['my-agent', 'my-agent-2', 'research-bot'], rosterRead: true });
		act(() => result.current.reset());
		expect(result.current.name).toBe('my-agent-3');
	});
});
