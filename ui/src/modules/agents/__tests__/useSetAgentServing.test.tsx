/**
 * useSetAgentServing — the dock serving toggle's mutation. Pins the cancellation
 * contract: a roster refetch that was merely SUPERSEDED (TanStack's
 * CancelledError) resolves as full success, since the replacement fetch refreshes
 * the view the mutation awaited. Only a real failure is a ServingRefreshError.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useState } from 'react';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import { ServingRefreshError, useAgents, useSetAgentServing } from '@/modules/agents/api';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';

function Harness() {
	// Materialise the roster query the mutation refetches.
	const roster = useAgents({ status: 'all' });
	const setServing = useSetAgentServing();
	const [result, setResult] = useState<string | null>(null);
	return (
		<div>
			<output data-testid="roster">{roster.isSuccess ? 'ready' : 'loading'}</output>
			<button
				type="button"
				onClick={(): void => {
					void setServing
						.mutateAsync({ id: 'agnt_active_1', serving: false })
						.then(() => setResult('success'))
						.catch((e: unknown) =>
							setResult(
								e instanceof ServingRefreshError ? 'refresh-error' : 'other-error',
							),
						);
				}}
			>
				Toggle
			</button>
			<output data-testid="result">{result}</output>
		</div>
	);
}

describe('useSetAgentServing — superseded roster refetch', () => {
	beforeEach(() => {
		setToken('test-token');
		resetAgentsStore();
	});

	it('treats a cancelled (superseded) refetch as success, not a refresh failure', async () => {
		let agentsCalls = 0;
		let releaseHang!: () => void;
		const hang = new Promise<void>((resolve) => {
			releaseHang = resolve;
		});
		worker.use(
			http.get('/agents', async () => {
				agentsCalls += 1;
				// Call 2 is the mutation's awaited refetch, held open so a competing
				// refetch can supersede (cancel) it; call 3 is the superseding fetch.
				if (agentsCalls === 2) await hang;
				return HttpResponse.json({ data: [], has_more: false, next_cursor: null });
			}),
		);
		const user = userEvent.setup();
		const { queryClient } = renderWithProviders(<Harness />);
		await waitFor(() => expect(screen.getByTestId('roster')).toHaveTextContent('ready'));

		await user.click(screen.getByRole('button', { name: 'Toggle' }));
		// The write has landed and the mutation's refetch is in flight (hung).
		await waitFor(() => expect(agentsCalls).toBe(2));

		// A competing invalidation supersedes the in-flight refetch: TanStack cancels it
		// (CancelledError) and starts a replacement that completes fine.
		await queryClient.invalidateQueries({ queryKey: agentsKeysForTest.lists() });

		// The write is fully successful — never "the fleet view could not
		// refresh", because the replacement fetch refreshed it.
		await waitFor(() => expect(screen.getByTestId('result')).toHaveTextContent('success'));

		releaseHang();
	});
});
