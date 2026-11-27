import { beforeEach, describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import {
	RenewBindingControl,
	type RenewBindingControlProps,
} from '@/shared/credentials/components/RenewBindingControl';

const DAY = 86_400;

function props(over: Partial<RenewBindingControlProps> = {}): RenewBindingControlProps {
	return {
		agentId: 'agnt_1',
		agentName: 'billing-ops',
		credentialId: 'cred_1',
		expiresAt: '2030-01-01T00:00:00Z',
		expired: false,
		maxIdlePeriod: 14 * DAY,
		...over,
	};
}

describe('RenewBindingControl — what a renewal would do', () => {
	beforeEach(() => setToken('test-token'));

	it('lets the warning be ticked on an expired binding, whose new end the server works out', async () => {
		renderWithProviders(
			<RenewBindingControl
				{...props({
					expiresAt: '2026-01-01T00:00:00Z',
					expired: true,
					maxIdlePeriod: null,
				})}
			/>,
		);

		// Nothing chosen yet, and a limit changed: the server runs the binding again
		// for as long as it was granted for, so the old end date (long past) says
		// nothing about whether the new one is worth warning about.
		await userEvent.click(
			screen.getByTestId('renew-binding-single-use').querySelector('input')!,
		);

		expect(await screen.findByTestId('renew-binding-idle-warning')).toBeVisible();
	});

	it('says the warning is only sent for access of 4 days or more while the end is the server’s choice', async () => {
		renderWithProviders(
			<RenewBindingControl
				{...props({
					expiresAt: '2026-01-01T00:00:00Z',
					expired: true,
					maxIdlePeriod: null,
				})}
			/>,
		);
		await userEvent.click(
			screen.getByTestId('renew-binding-single-use').querySelector('input')!,
		);

		const hint = await screen.findByTestId('renew-binding-idle-warning-hint');
		expect(hint).toHaveTextContent('Only sent if the renewed access lasts 4 days or more.');
		// A chosen end date is known, so there is nothing to caveat.
		await userEvent.selectOptions(screen.getByTestId('renew-binding-duration-preset'), '30d');
		expect(screen.getByTestId('renew-binding-idle-warning-hint')).not.toHaveTextContent(
			'Only sent',
		);
	});

	it('starts over from the binding when it changes elsewhere, rather than writing old values back', async () => {
		const patches: Record<string, unknown>[] = [];
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', async ({ request }) => {
				patches.push((await request.json()) as Record<string, unknown>);
				return HttpResponse.json({});
			}),
		);
		const { rerender } = renderWithProviders(<RenewBindingControl {...props()} />);
		expect(screen.getByTestId('renew-binding-idle-preset')).toHaveValue('14d');

		// Another tab renews it with a 30-day idle limit.
		rerender(<RenewBindingControl {...props({ maxIdlePeriod: 30 * DAY })} />);

		await waitFor(() =>
			expect(screen.getByTestId('renew-binding-idle-preset')).toHaveValue('30d'),
		);
		// Nothing differs from the binding now, so there is nothing to save: the
		// stale 14 days is not written back over the 30 just set.
		expect(screen.getByTestId('renew-binding-save')).toBeDisabled();
		expect(patches).toHaveLength(0);
	});
});
