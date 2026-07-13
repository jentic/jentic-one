import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { AccessRequestDialog } from '@/shared/app/rail/AccessRequestDialog';

// Note on a11y: the terminal screen renders inside a framer-motion entrance
// animation (initial opacity 0 → 1). In headless tests axe measures the text
// mid-fade and reports a false color-contrast failure on EVERY outcome screen
// (granted/declined/error alike), so checkA11y isn't meaningful here. The markup
// is accessible by construction: role="status" container, a real <h2>, and list
// semantics for the blocked items.

/**
 * The dialog's terminal screen must reflect the SERVER's authoritative per-item
 * outcome, not the operator's draft intent: the backend can override an
 * "approved" verdict to "denied" when a bind target can't be fulfilled as filed
 * (e.g. no toolkit serves the API yet). These tests register per-request MSW
 * handlers (the repo convention) whose `:decide` response returns a chosen
 * decided state regardless of the submitted body, simulating that override.
 */

type Item = {
	id: string;
	resource_type: string;
	action: string;
	status: string;
	resource_id?: string | null;
	resource_reference?: Record<string, unknown> | null;
	decision_reason?: string | null;
};

function request(id: string, status: string, items: Item[]) {
	return {
		id,
		actor_id: 'agnt_1',
		status,
		requested_by: 'usr_1',
		filed_at: new Date().toISOString(),
		expires_at: new Date(Date.now() + 3_600_000).toISOString(),
		items,
		evaluation: { can_fulfill: true, checks: [] },
	};
}

const bindItem = (status: string, decision_reason: string | null = null): Item => ({
	id: 'arqi_bind',
	resource_type: 'toolkit',
	action: 'bind',
	status,
	resource_reference: { vendor: 'googleapis-com', name: 'googleapis-com-sheets' },
	decision_reason,
});

const scopeItem = (status: string): Item => ({
	id: 'arqi_scope',
	resource_type: 'scope',
	action: 'grant',
	status,
	resource_id: 'apis:write',
});

/**
 * Stub GET (loads the pending request) and `:decide` (returns the server's
 * authoritative decided request). `worker.use` handlers take priority over the
 * default rail handlers and are reset after each test by the global setup.
 */
function stub(getRequest: object, decideResponse: object) {
	worker.use(
		http.get('/access-requests/:id', () => HttpResponse.json(getRequest)),
		http.post(/\/access-requests\/([^/]+):decide$/, () => HttpResponse.json(decideResponse)),
	);
}

async function approveAndSubmit() {
	const user = userEvent.setup();
	await user.click(await screen.findByRole('button', { name: 'Approve' }));
	await user.click(screen.getByRole('button', { name: /Review & submit/i }));
	await user.click(screen.getByRole('button', { name: /Confirm decision/i }));
}

async function approveAllAndSubmit() {
	const user = userEvent.setup();
	await user.click(await screen.findByRole('button', { name: /Approve all/i }));
	await user.click(screen.getByRole('button', { name: /Review & submit/i }));
	await user.click(screen.getByRole('button', { name: /Confirm decision/i }));
}

describe('AccessRequestDialog — server-authoritative outcome', () => {
	it('shows the platform reason instead of "granted" when an approved bind is denied server-side', async () => {
		const reason =
			'No toolkit serves API googleapis-com/googleapis-com-sheets; provision and bind a credential for it first';
		stub(
			request('areq_1', 'pending', [bindItem('pending')]),
			request('areq_1', 'denied', [bindItem('denied', reason)]),
		);

		renderWithProviders(<AccessRequestDialog requestId="areq_1" open onClose={() => {}} />);
		await approveAndSubmit();

		expect(await screen.findByText('Could not grant access')).toBeInTheDocument();
		expect(screen.getByText(reason)).toBeInTheDocument();
		expect(screen.queryByText('Access granted')).not.toBeInTheDocument();
	});

	it('reports success when the server actually approves the item', async () => {
		stub(
			request('areq_1', 'pending', [bindItem('pending')]),
			request('areq_1', 'approved', [bindItem('approved')]),
		);

		renderWithProviders(<AccessRequestDialog requestId="areq_1" open onClose={() => {}} />);
		await approveAndSubmit();

		expect(await screen.findByText('Access granted')).toBeInTheDocument();
		expect(screen.queryByText('Could not grant access')).not.toBeInTheDocument();
	});

	it('reports partial success and lists the unfulfilled item on a mixed decision', async () => {
		// Two approved items: the scope grant is fulfilled, the toolkit bind is
		// not. Outcome is "Access granted" (something was granted) BUT the blocked
		// item's reason must still be surfaced — not silently dropped.
		const reason = 'No toolkit serves API googleapis-com/googleapis-com-sheets';
		stub(
			request('areq_2', 'pending', [scopeItem('pending'), bindItem('pending')]),
			request('areq_2', 'partially_approved', [
				scopeItem('approved'),
				bindItem('denied', reason),
			]),
		);

		renderWithProviders(<AccessRequestDialog requestId="areq_2" open onClose={() => {}} />);
		await approveAllAndSubmit();

		expect(await screen.findByText('Access granted')).toBeInTheDocument();
		expect(screen.getByText(reason)).toBeInTheDocument();
	});
});
