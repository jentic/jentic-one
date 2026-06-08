import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { JobBadge } from '@/components/monitor/execution-log/JobBadge';

/**
 * JobBadge is a tiny inline cross-link rendered in the Execution Log next to
 * an operation that came from an async job. It needs to:
 *   - Show a humanised, truncated suffix derived from the `job_*` id.
 *   - Forward clicks to the parent's `onOpen` handler with the full id.
 *   - NOT bubble click events to the surrounding row's onClick — otherwise
 *     clicking the badge would also open the row's detail sheet.
 */
describe('JobBadge', () => {
	it('renders a 6-char suffix derived from the job_<id> prefix', () => {
		render(<JobBadge jobId="job_abc123xyz789" />);
		// Display strips `job_` prefix and truncates to 6 chars.
		expect(screen.getByText(/job·abc123/)).toBeInTheDocument();
	});

	it('uses the raw id when there is no job_ prefix', () => {
		render(<JobBadge jobId="custom" />);
		expect(screen.getByText(/job·custom/)).toBeInTheDocument();
	});

	it('forwards the full job id to onOpen on click', () => {
		const onOpen = vi.fn();
		render(<JobBadge jobId="job_xyz" onOpen={onOpen} />);
		fireEvent.click(screen.getByRole('button', { name: /open job job_xyz/i }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onOpen).toHaveBeenCalledWith('job_xyz');
	});

	it('stops click propagation so the surrounding row click does not also fire', () => {
		const onRowClick = vi.fn();
		const onOpen = vi.fn();
		render(
			<div onClick={onRowClick} role="row">
				<JobBadge jobId="job_isolated" onOpen={onOpen} />
			</div>,
		);
		fireEvent.click(screen.getByRole('button', { name: /open job job_isolated/i }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onRowClick).not.toHaveBeenCalled();
	});

	it('still renders without onOpen — click is then a no-op', () => {
		render(<JobBadge jobId="job_no_handler" />);
		const btn = screen.getByRole('button', { name: /open job job_no_handler/i });
		// Should not throw on click.
		expect(() => fireEvent.click(btn)).not.toThrow();
	});
});
