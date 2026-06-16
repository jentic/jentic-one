import { describe, it, expect, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/__tests__/mocks/browser';
import { TestConnectionButton } from '@/components/credentials/TestConnectionButton';

/**
 * The branch is named for this behaviour: clicking "Test connection" persists
 * a health verdict server-side (`credentials.healthy`), so the button must
 * invalidate the credential queries that back the StatusDot — otherwise the
 * pill stays stale until a manual page refresh.
 *
 * These tests pin the cache-invalidation contract: a successful test must
 * refetch BOTH the `['credentials']` list and the `['credential', id]` detail.
 */

function probeOk(status = 200) {
	worker.use(
		http.post('/credentials/:id/test', () =>
			HttpResponse.json({
				ok: true,
				status,
				hint: null,
				probe_url: 'https://api.example.com/health',
			}),
		),
	);
}

describe('TestConnectionButton', () => {
	it('invalidates the credentials list and the credential detail on success', async () => {
		probeOk();

		const { queryClient } = renderWithProviders(
			<TestConnectionButton credentialId="cred-42" />,
		);
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

		await userEvent.click(screen.getByRole('button', { name: /test connection/i }));

		await waitFor(() =>
			expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['credentials'] }),
		);
		expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['credential', 'cred-42'] });
	});

	it('renders the success pill after a green probe', async () => {
		probeOk();

		renderWithProviders(<TestConnectionButton credentialId="cred-1" />);
		await userEvent.click(screen.getByRole('button', { name: /test connection/i }));

		expect(await screen.findByText(/looks good/i)).toBeInTheDocument();
	});

	it('renders the rejected pill on a 401 and still invalidates the detail', async () => {
		worker.use(
			http.post('/credentials/:id/test', () =>
				HttpResponse.json({
					ok: false,
					status: 401,
					hint: 'unauthorized',
					probe_url: 'https://api.example.com/health',
				}),
			),
		);

		const { queryClient } = renderWithProviders(<TestConnectionButton credentialId="cred-7" />);
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

		await userEvent.click(screen.getByRole('button', { name: /test connection/i }));

		expect(await screen.findByText(/credential rejected/i)).toBeInTheDocument();
		// A 401 still persists healthy=false server-side, so the pill must refresh.
		await waitFor(() =>
			expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['credential', 'cred-7'] }),
		);
	});

	it('surfaces a network error and does not invalidate on failure', async () => {
		worker.use(http.post('/credentials/:id/test', () => HttpResponse.error()));

		const { queryClient } = renderWithProviders(<TestConnectionButton credentialId="cred-9" />);
		const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

		await userEvent.click(screen.getByRole('button', { name: /test connection/i }));

		expect(await screen.findByText(/network error/i)).toBeInTheDocument();
		expect(invalidateSpy).not.toHaveBeenCalled();
	});
});
