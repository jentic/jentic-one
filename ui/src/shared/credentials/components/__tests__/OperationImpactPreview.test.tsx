import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { checkA11y, renderWithProviders, screen, userEvent, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { OperationImpactPreview } from '@/shared/credentials/components/OperationImpactPreview';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

const API = { vendor: 'api.example.com', name: 'example', version: '1' };

const OPS = [
	{ operation_id: 'repos/get', method: 'GET', path: '/repos/{owner}/{repo}' },
	{ operation_id: 'issues/create', method: 'POST', path: '/repos/{owner}/{repo}/issues' },
	{ operation_id: 'repos/update', method: 'PATCH', path: '/repos/{owner}/{repo}' },
	{ operation_id: 'repos/delete', method: 'DELETE', path: '/repos/{owner}/{repo}' },
];

const RULES: PermissionRule[] = [
	{ effect: 'allow', methods: ['GET'], path: '/repos', match_mode: 'prefix' },
	{ effect: 'require-approval', methods: ['POST'], path: '/repos', match_mode: 'prefix' },
	// Only one owner's repos are held; every other owner falls to default-deny.
	{
		effect: 'require-approval',
		methods: ['PATCH'],
		path: '/repos/jentic/{repo}',
		match_mode: 'exact',
	},
];

function serveOps() {
	worker.use(
		http.get('*/apis/:vendor/:name/:version/operations', () =>
			HttpResponse.json({ data: OPS, has_more: false, next_cursor: null }),
		),
	);
}

/** The leaf row (op path + method) inside an expanded group. */
function leaf(method: string, path: string): HTMLElement {
	const row = screen
		.getAllByText(path)
		.map((el) => el.closest('div.rounded-md') as HTMLElement)
		.find((r) => within(r).queryByText(method) != null);
	if (!row) throw new Error(`no ${method} ${path} row`);
	return row;
}

describe('OperationImpactPreview', () => {
	it('shows require-approval coverage as needing approval, not deny', async () => {
		serveOps();
		const user = userEvent.setup();
		const { container } = renderWithProviders(
			<OperationImpactPreview api={API} rules={RULES} />,
		);

		const header = await screen.findByRole('button', { name: /\/repos\// });
		expect(within(header).getByText('1 allowed')).toBeInTheDocument();
		// POST is held outright; PATCH is held for one owner and denied otherwise.
		expect(within(header).getByText('2 ask')).toBeInTheDocument();
		expect(within(header).getByText('1 denied')).toBeInTheDocument();

		await user.click(header);
		expect(within(leaf('GET', '/repos/{owner}/{repo}')).getByLabelText('allow')).toBeVisible();
		expect(
			within(leaf('POST', '/repos/{owner}/{repo}/issues')).getByLabelText('ask'),
		).toBeVisible();
		expect(
			within(leaf('DELETE', '/repos/{owner}/{repo}')).getByLabelText('deny'),
		).toBeVisible();

		const patch = leaf('PATCH', '/repos/{owner}/{repo}');
		expect(within(patch).getByLabelText('partial')).toBeVisible();
		await user.click(patch);
		expect(within(patch).getByText('/repos/jentic/example-repo')).toBeVisible();
		expect(within(patch).getByText('/repos/example-owner/example-repo')).toBeVisible();
		expect(within(patch).getByText('ask')).toBeVisible();

		await checkA11y(container);
	});

	it('sorts leaf rows allowed, partial, ask, then denied', async () => {
		serveOps();
		const user = userEvent.setup();
		renderWithProviders(<OperationImpactPreview api={API} rules={RULES} />);
		await user.click(await screen.findByRole('button', { name: /\/repos\// }));
		const pills = ['allow', 'partial', 'ask', 'deny'].map((label) =>
			screen.getByLabelText(label),
		);
		for (let i = 1; i < pills.length; i++) {
			expect(
				pills[i - 1].compareDocumentPosition(pills[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
			).toBeTruthy();
		}
	});
});
