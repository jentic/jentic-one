/**
 * The review pop-up on a call held because the agent's access ended
 * (`kind: grant_renewal`): it says why access ended, and its decision is
 * re-granted access — chosen with the same controls as granting it, then
 * re-granted with or without running the call, or denied. A reviewer who may
 * not renew gets Deny only; where the hold can't renew (a split deployment),
 * the binding is renewed through its own endpoint first, then the call is
 * approved without a policy. Bulk approve sets renewals aside.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { Toaster } from '@/shared/ui';
import { seedCredentialBindings } from '@/modules/agents/mocks/handlers';
import { ApprovalReviewHost } from '@/shared/approvals';
import {
	clearApprovalsStore,
	getMockApproval,
	seedMockApproval,
} from '@/modules/agents/mocks/approvalsHandlers';

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
const hoursFromNow = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString();

let decisions: { id: string; body: Record<string, unknown> }[];

beforeEach(() => {
	clearApprovalsStore();
	decisions = [];
	worker.use(
		http.get('/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({
				data: [{ operation_id: 'createCharge', name: 'Create a charge' }],
				has_more: false,
				next_cursor: null,
			}),
		),
		// Records what each decision sends, then falls through to the store.
		http.post('/executions/holds/:id\\:decide', async ({ request, params }) => {
			decisions.push({
				id: String(params.id),
				body: (await request.clone().json()) as Record<string, unknown>,
			});
		}),
	);
});

function seedRenewal(id: string, canRenew: boolean, renewOnDecide = true) {
	return seedMockApproval({
		id,
		kind: 'grant_renewal',
		agent_id: 'agnt_billing',
		agent_name: 'billing-ops',
		agent_owner_id: 'usr_owner',
		operation_id: 'createCharge',
		created_at: minutesAgo(5),
		expires_at: hoursFromNow(23),
		renewal: {
			expired_reason: 'idle',
			last_used_at: new Date(Date.now() - 15 * 86_400_000).toISOString(),
			expires_at: null,
			max_idle_period: 'P14D',
			idle_warning: false,
			max_quota: null,
			quota_remaining: null,
			can_renew: canRenew,
			renew_on_decide: renewOnDecide,
		},
	});
}

function renderHost(route: string) {
	return renderWithProviders(
		<>
			<ApprovalReviewHost />
			<Toaster />
		</>,
		{ route },
	);
}

describe('review pop-up on a call held because access ended', () => {
	it('says why access ended and re-grants access, then runs the call', async () => {
		seedRenewal('held_renew', true);
		const { container } = renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByTestId('renewal-ended')).toHaveTextContent(
			'Unused for 14 days',
		);
		expect(within(dialog).getByTestId('renewal-last-used')).toHaveTextContent('15d ago');
		// The plain decision bar gives way to the renewal's own.
		expect(within(dialog).queryByRole('button', { name: 'Approve and run' })).toBeNull();
		const grant = within(dialog).getByTestId('renewal-grant-and-run');
		// Nothing preselected: re-granting waits for a choice.
		expect(grant).toBeDisabled();
		await userEvent.selectOptions(within(dialog).getByTestId('renewal-duration-preset'), '7d');
		await userEvent.click(within(dialog).getByText('End after one use'));
		expect(within(dialog).getByTestId('renewal-access-summary')).toHaveTextContent(
			/^Access ends after one use, or around .+ if not used by then\.$/,
		);
		await checkA11y(container, { modal: true });
		await userEvent.click(grant);

		await waitFor(() => expect(decisions).toHaveLength(1));
		const body = decisions[0].body as {
			decision: string;
			run: boolean;
			renewal: {
				expires_at: string;
				max_quota: number;
				max_idle_period: string | null;
				after_expiry: string;
			};
		};
		expect(body).toMatchObject({ decision: 'approve', run: true });
		expect(body.renewal).toMatchObject({
			max_quota: 1,
			max_idle_period: null,
			after_expiry: 'hold',
		});
		const days = (Date.parse(body.renewal.expires_at) - Date.now()) / 86_400_000;
		expect(days).toBeGreaterThan(6.9);
		expect(getMockApproval('held_renew')?.execution_status).toBe('queued');
		// The last waiting call: the pop-up closes with one toast.
		expect(
			await screen.findByText(/^Re-granted access · .?Create a charge.?$/u),
		).toBeInTheDocument();
	});

	it('sends the idle limit as an ISO 8601 duration, and can re-grant without running', async () => {
		seedRenewal('held_renew', true);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await userEvent.selectOptions(
			await within(dialog).findByTestId('renewal-duration-preset'),
			'30d',
		);
		await userEvent.click(within(dialog).getByText('Also end if unused for'));
		await userEvent.selectOptions(within(dialog).getByTestId('renewal-idle-preset'), '14d');
		await userEvent.click(within(dialog).getByTestId('renewal-grant'));

		await waitFor(() => expect(decisions).toHaveLength(1));
		expect(decisions[0].body).toMatchObject({
			decision: 'approve',
			run: false,
			renewal: { max_idle_period: 'P14D' },
		});
	});

	it('says the call is held because access ended, not that an Ask rule holds it', async () => {
		seedRenewal('held_renew', true);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(
			await within(dialog).findByText(/Held because this agent's access to/),
		).toHaveTextContent("Held because this agent's access to payments ended.");
		expect(
			within(dialog).getByRole('link', { name: /Review the agent's access/ }),
		).toBeVisible();
		expect(within(dialog).queryByText(/Ask rule/)).toBeNull();
		expect(within(dialog).queryByRole('link', { name: /Review the agent's rules/ })).toBeNull();
	});

	it('still says an Ask rule holds an ordinary held call', async () => {
		seedMockApproval({
			id: 'held_plain',
			agent_id: 'agnt_billing',
			agent_name: 'billing-ops',
			operation_id: 'createCharge',
			created_at: minutesAgo(3),
			expires_at: hoursFromNow(23),
		});
		renderHost('/agents?review=held_plain');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(
			await within(dialog).findByText(/An Ask rule on this agent's access to/),
		).toBeVisible();
		expect(
			within(dialog).getByRole('link', { name: /Review the agent's rules/ }),
		).toBeVisible();
		expect(within(dialog).queryByText(/Held because this agent's access/)).toBeNull();
	});

	it('offers only Deny to a reviewer who may not renew', async () => {
		seedRenewal('held_renew', false);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByTestId('renewal-cannot-renew')).toBeVisible();
		expect(within(dialog).queryByTestId('renewal-grant')).toBeNull();
		await userEvent.click(within(dialog).getByTestId('renewal-deny'));

		await waitFor(() => expect(decisions).toHaveLength(1));
		expect(decisions[0].body).toEqual({ decision: 'deny', reason: null });
		expect(getMockApproval('held_renew')?.state).toBe('denied');
	});

	it('renews through the binding, then approves, where the hold cannot renew', async () => {
		seedRenewal('held_renew', false, false);
		const renewals: { expires_at: string | null }[] = [];
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', async ({ params, request }) => {
				const body = (await request.json()) as { expires_at: string | null };
				renewals.push(body);
				return HttpResponse.json({
					id: 'acb_1',
					agent_id: params.aid,
					credential_id: params.cid,
					bound_at: '2026-05-02T09:00:00Z',
					suspended: false,
					expires_at: body.expires_at,
					expired: false,
				});
			}),
		);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByTestId('renewal-elsewhere')).toBeVisible();
		await userEvent.selectOptions(within(dialog).getByTestId('renewal-duration-preset'), '7d');
		await userEvent.click(within(dialog).getByTestId('renewal-grant-and-run'));

		await waitFor(() => expect(decisions).toHaveLength(1));
		expect(renewals).toHaveLength(1);
		const days = (Date.parse(renewals[0].expires_at!) - Date.now()) / 86_400_000;
		expect(days).toBeGreaterThan(6.9);
		expect(decisions[0].body).toEqual({ decision: 'approve', run: true });
		expect(getMockApproval('held_renew')?.execution_status).toBe('queued');
	});
});

describe('re-granting access in the dev mock', () => {
	it('renews the binding itself, so the console stops showing it as ended', async () => {
		seedRenewal('held_renew', true);
		const credentialId = getMockApproval('held_renew')!.credential_id;
		seedCredentialBindings([
			{
				agent_id: 'agnt_billing',
				credential_id: credentialId,
				expires_at: new Date(Date.now() - 86_400_000).toISOString(),
			},
		]);
		const ended = async () =>
			(
				(await (await fetch(`/credentials/${credentialId}/agents`)).json()) as {
					data: { agent_id: string; expired: boolean; expires_at: string | null }[];
				}
			).data.find((row) => row.agent_id === 'agnt_billing');
		expect((await ended())?.expired).toBe(true);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		await userEvent.selectOptions(
			await within(dialog).findByTestId('renewal-duration-preset'),
			'7d',
		);
		await userEvent.click(within(dialog).getByTestId('renewal-grant-and-run'));

		await waitFor(() => expect(getMockApproval('held_renew')?.state).toBe('approved'));
		const after = await ended();
		expect(after?.expired).toBe(false);
		expect(Date.parse(after!.expires_at!)).toBeGreaterThan(Date.now());
	});
});

describe('a renewal made through the binding, then approved', () => {
	function bindingRenewals(): { count: number } {
		const seen = { count: 0 };
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', async ({ params, request }) => {
				seen.count += 1;
				const body = (await request.json()) as { expires_at: string | null };
				return HttpResponse.json({
					id: 'acb_1',
					agent_id: params.aid,
					credential_id: params.cid,
					bound_at: '2026-05-02T09:00:00Z',
					suspended: false,
					expires_at: body.expires_at,
					expired: false,
				});
			}),
		);
		return seen;
	}

	it('says both outcomes when the approval fails, and retries the approval alone', async () => {
		seedRenewal('held_renew', false, false);
		const renewals = bindingRenewals();
		let failApproval = true;
		worker.use(
			http.post('/executions/holds/:id\\:decide', () =>
				failApproval
					? HttpResponse.json(
							{ type: 'internal', title: 'Boom', status: 500, detail: 'Try again.' },
							{ status: 500 },
						)
					: undefined,
			),
		);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByTestId('renewal-elsewhere')).toBeVisible();
		await userEvent.selectOptions(within(dialog).getByTestId('renewal-duration-preset'), '7d');
		await userEvent.click(within(dialog).getByTestId('renewal-grant-and-run'));

		// Access is live already, and the reviewer is told so rather than only that it failed.
		expect(await within(dialog).findByText(/Access was renewed, but the call/)).toBeVisible();
		expect(renewals.count).toBe(1);

		// Retrying decides the call again; it never renews a second time.
		failApproval = false;
		await userEvent.click(within(dialog).getByTestId('renewal-grant-and-run'));
		await waitFor(() => expect(getMockApproval('held_renew')?.state).toBe('approved'));
		expect(renewals.count).toBe(1);
	});

	it('holds the stepper still while the binding is being renewed', async () => {
		seedRenewal('held_renew', false, false);
		seedRenewal('held_other', false, false);
		let release: () => void = () => {};
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		worker.use(
			http.patch('/agents/:aid/credentials/:cid', async () => {
				await gate;
				return HttpResponse.json({
					id: 'acb_1',
					agent_id: 'agnt_billing',
					credential_id: 'cred_1',
					bound_at: '2026-05-02T09:00:00Z',
					suspended: false,
					expires_at: null,
					expired: false,
				});
			}),
		);
		renderHost('/agents?review=held_renew');

		const dialog = await screen.findByRole('dialog', { name: 'Review call' });
		expect(await within(dialog).findByTestId('renewal-elsewhere')).toBeVisible();
		await userEvent.selectOptions(within(dialog).getByTestId('renewal-duration-preset'), '7d');
		await userEvent.click(within(dialog).getByTestId('renewal-grant-and-run'));

		// Mid-renewal, moving to another call would decide the wrong one afterwards.
		const next = await within(dialog).findByRole('button', { name: /^Next/ });
		await waitFor(() => expect(next).toBeDisabled());
		release();
	});
});

describe('bulk approve', () => {
	it('sets a re-grant aside, to decide one at a time', async () => {
		seedRenewal('held_renew', true);
		seedMockApproval({
			id: 'held_plain',
			agent_id: 'agnt_billing',
			agent_name: 'billing-ops',
			operation_id: 'createCharge',
			created_at: minutesAgo(3),
			expires_at: hoursFromNow(23),
		});
		renderHost('/agents?review_batch=held_renew,held_plain');

		const dialog = await screen.findByRole('dialog');
		expect(await within(dialog).findByRole('button', { name: /Approve 1/ })).toBeVisible();
	});
});
