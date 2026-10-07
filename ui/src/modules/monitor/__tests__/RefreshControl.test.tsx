import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/__tests__/test-utils';
import { formatAge, RefreshControl } from '@/modules/monitor/components/RefreshControl';

describe('formatAge', () => {
	it('reads freshness in coarse steps', () => {
		expect(formatAge(3_000)).toBe('just now');
		expect(formatAge(17_000)).toBe('15s ago');
		expect(formatAge(125_000)).toBe('2m ago');
		expect(formatAge(2 * 3_600_000)).toBe('2h ago');
	});
});

describe('RefreshControl', () => {
	it('shows how fresh the data is', () => {
		renderWithProviders(
			<RefreshControl
				updatedAt={Date.now()}
				onRefresh={async () => {}}
				intervalMs={30_000}
			/>,
		);
		expect(screen.getByText('just now')).toBeInTheDocument();
	});

	it('refreshes on click and announces when done', async () => {
		const user = userEvent.setup();
		let resolve!: () => void;
		const onRefresh = vi.fn(() => new Promise<void>((r) => (resolve = r)));
		renderWithProviders(
			<RefreshControl updatedAt={Date.now()} onRefresh={onRefresh} intervalMs={30_000} />,
		);
		const button = screen.getByRole('button', { name: 'Refresh usage' });
		await user.click(button);
		expect(onRefresh).toHaveBeenCalledTimes(1);
		expect(button).toHaveAttribute('aria-busy', 'true');

		// A second click while in flight doesn't stack another request.
		await user.click(button);
		expect(onRefresh).toHaveBeenCalledTimes(1);

		resolve();
		await waitFor(() =>
			expect(screen.getByRole('status')).toHaveTextContent('Usage refreshed.'),
		);
		expect(button).toHaveAttribute('aria-busy', 'false');
	});
});
