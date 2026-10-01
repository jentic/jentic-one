import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, waitFor, within } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import { BindAgentDialog } from '@/modules/workspace/components/BindAgentDialog';

/**
 * "Already bound" must come from EVERY page of the credential's agents: an
 * agent past the first page must not read as unbound (and be bound twice),
 * and nothing is bindable until the roster is whole.
 */

const CRED = makeMockCredential({
	credential_id: 'cred_big',
	name: 'Big key',
	api: { vendor: 'stripe', name: 'stripe-api', version: '' },
});

function pickerAgent(id: string) {
	return {
		id,
		name: id,
		status: 'active',
		created_at: '2026-01-01T00:00:00Z',
	};
}

function boundAgent(id: string) {
	return {
		agent_id: id,
		agent_name: id,
		bound_at: '2026-01-01T00:00:00Z',
		rule_set_id: null,
		status: 'active',
		suspended: false,
	};
}

let posted: string[] = [];

/** Two bound pages: `agent_1` on page 1, `agent_2` only on page 2. */
function serve(opts: { page2?: 'ok' | 'error' | 'hang' } = {}) {
	const page2 = opts.page2 ?? 'ok';
	worker.use(
		http.get('/agents', () =>
			HttpResponse.json({
				data: ['agent_1', 'agent_2', 'agent_3'].map(pickerAgent),
				has_more: false,
				next_cursor: null,
			}),
		),
		http.get('/credentials/:cid/agents', async ({ request }) => {
			const cursor = new URL(request.url).searchParams.get('cursor');
			if (!cursor) {
				return HttpResponse.json({
					data: [boundAgent('agent_1')],
					has_more: true,
					next_cursor: 'page-2',
				});
			}
			if (page2 === 'error') {
				return HttpResponse.json({ detail: 'boom' }, { status: 500 });
			}
			if (page2 === 'hang') await new Promise(() => {});
			return HttpResponse.json({
				data: [boundAgent('agent_2')],
				has_more: false,
				next_cursor: null,
			});
		}),
		http.post('/agents/:id/credentials', ({ params }) => {
			posted.push(String(params.id));
			return HttpResponse.json({}, { status: 201 });
		}),
	);
}

function renderDialog() {
	return renderWithProviders(
		<BindAgentDialog open onClose={() => {}} credentials={[CRED]} apiLabel="Stripe" />,
	);
}

function optionFor(name: string): HTMLElement {
	const row = screen
		.getAllByTestId('bind-agent-option')
		.find((li) => within(li).queryByText(name) != null);
	if (!row) throw new Error(`no option for ${name}`);
	return row;
}

describe('BindAgentDialog — already-bound agents across pages', () => {
	beforeEach(() => {
		setToken('test-token');
		posted = [];
	});

	it('marks an agent bound only on page 2 as already bound', async () => {
		serve();
		renderDialog();
		await waitFor(() =>
			expect(within(optionFor('agent_2')).getByText('Already bound')).toBeInTheDocument(),
		);
		expect(within(optionFor('agent_1')).getByText('Already bound')).toBeInTheDocument();
		expect(within(optionFor('agent_3')).queryByText('Already bound')).not.toBeInTheDocument();
		expect(within(optionFor('agent_2')).getByRole('checkbox')).toBeDisabled();

		const user = userEvent.setup();
		await user.click(within(optionFor('agent_3')).getByRole('checkbox'));
		await user.click(screen.getByTestId('bind-agent-confirm'));
		await waitFor(() => expect(posted).toEqual(['agent_3']));
	});

	it('holds every tick and the confirm while the roster is still draining', async () => {
		serve({ page2: 'hang' });
		renderDialog();
		expect(await screen.findByTestId('bind-agent-checking')).toBeInTheDocument();
		expect(within(optionFor('agent_2')).getByRole('checkbox')).toBeDisabled();
		expect(within(optionFor('agent_3')).getByRole('checkbox')).toBeDisabled();
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});

	it('says so (with a retry) when a later page fails, and stays unbindable', async () => {
		serve({ page2: 'error' });
		renderDialog();
		expect(
			await screen.findByText('Couldn’t check which agents already use this credential.'),
		).toBeInTheDocument();
		expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
		expect(within(optionFor('agent_3')).getByRole('checkbox')).toBeDisabled();
		expect(screen.getByTestId('bind-agent-confirm')).toBeDisabled();
	});
});
