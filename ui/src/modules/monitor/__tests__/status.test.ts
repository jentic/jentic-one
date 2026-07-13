import { describe, it, expect } from 'vitest';
import { toExecutionStatus, toJobStatus, isTerminalJobStatus } from '@/modules/monitor/api/types';

describe('monitor status mappers', () => {
	describe('toExecutionStatus', () => {
		it('maps the backend terminal statuses to the UI union', () => {
			// ExecutionStatus is terminal-only on the wire: completed | failed.
			expect(toExecutionStatus('completed')).toBe('completed');
			expect(toExecutionStatus('failed')).toBe('failed');
		});

		it('keeps defensive fallbacks for statuses the backend may add later', () => {
			expect(toExecutionStatus('running')).toBe('running');
			expect(toExecutionStatus('in_progress')).toBe('running');
			expect(toExecutionStatus('cancelled')).toBe('cancelled');
			expect(toExecutionStatus('canceled')).toBe('cancelled');
		});

		it('is case-insensitive', () => {
			expect(toExecutionStatus('COMPLETED')).toBe('completed');
			expect(toExecutionStatus('Failed')).toBe('failed');
		});

		it('degrades unknown server values to "unknown" rather than crashing', () => {
			expect(toExecutionStatus('some_new_status')).toBe('unknown');
			expect(toExecutionStatus('')).toBe('unknown');
		});
	});

	describe('toJobStatus', () => {
		it('maps the backend JobStatus enum to the UI union', () => {
			expect(toJobStatus('queued')).toBe('queued');
			expect(toJobStatus('running')).toBe('running');
			expect(toJobStatus('completed')).toBe('completed');
			expect(toJobStatus('failed')).toBe('failed');
			expect(toJobStatus('cancelled')).toBe('cancelled');
			expect(toJobStatus('dead_letter')).toBe('dead_letter');
		});

		it('degrades unknown values to "unknown"', () => {
			expect(toJobStatus('weird')).toBe('unknown');
		});
	});

	describe('isTerminalJobStatus', () => {
		it('treats completed/failed/cancelled/dead_letter as terminal', () => {
			expect(isTerminalJobStatus('completed')).toBe(true);
			expect(isTerminalJobStatus('failed')).toBe(true);
			expect(isTerminalJobStatus('cancelled')).toBe(true);
			expect(isTerminalJobStatus('dead_letter')).toBe(true);
		});

		it('treats queued/running/unknown as non-terminal', () => {
			expect(isTerminalJobStatus('queued')).toBe(false);
			expect(isTerminalJobStatus('running')).toBe(false);
			expect(isTerminalJobStatus('unknown')).toBe(false);
		});
	});
});
