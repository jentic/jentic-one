import { describe, it, expect, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { WorkspaceSummaryBar } from '@/modules/discover/components/WorkspaceSummaryBar';
import type { WorkspaceDigest, WorkspaceDigestRow } from '@/modules/discover/api';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';

function row(name: string, createdAt: string, extra: Partial<WorkspaceDigestRow> = {}) {
	return makeDigestRow(name, { createdAt, ...extra });
}

function digestWith(partial: Partial<WorkspaceDigest> = {}): WorkspaceDigest {
	const rows = [row('Stripe', '2026-01-01T00:00:00Z'), row('Slack', '2026-03-01T00:00:00Z')];
	return {
		rows,
		attention: [],
		attentionComplete: true,
		attentionSettled: true,
		byCatalogApiId: new Map(),
		totals: { apis: rows.length, live: rows.length, draft: 0 },
		usageAvailable: false,
		usageExhaustive: false,
		credentialsError: false,
		isPending: false,
		error: null,
		complete: true,
		retry: () => {},
		...partial,
	};
}

describe('WorkspaceSummaryBar', () => {
	it('summarises the workspace with a neutral "All good" when nothing needs attention', () => {
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestWith()}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		const bar = screen.getByTestId('workspace-summary-bar');
		expect(bar).toHaveTextContent(/Your workspace.*2 APIs.*All good/);
		expect(screen.queryByTestId('workspace-summary-attention')).not.toBeInTheDocument();
		expect(bar).toHaveAttribute('aria-expanded', 'false');
		expect(bar).toHaveAttribute('aria-haspopup', 'dialog');
	});

	it('counts distinct APIs needing attention and shows pending imports', () => {
		const d = digestWith();
		const [stripe, slack] = d.rows;
		const digest = digestWith({
			attention: [
				{
					id: 'updates',
					label: 'upstream update available',
					rows: [stripe],
					tab: 'overview',
				},
				{ id: 'failures', label: 'failed calls', rows: [stripe, slack], tab: 'overview' },
			],
		});
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digest}
				pendingImports={[{ apiId: 'petstore', label: 'Petstore' }]}
				onImportOwn={() => {}}
			/>,
		);
		expect(screen.getByTestId('workspace-summary-attention')).toHaveTextContent(
			'2 need attention',
		);
		expect(screen.getByTestId('workspace-summary-importing')).toHaveTextContent('Adding 1…');
		expect(screen.queryByTestId('workspace-summary-all-good')).not.toBeInTheDocument();
	});

	it('opens the full panel in a bottom sheet; Escape closes it and focus returns to the bar', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestWith()}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		const bar = screen.getByTestId('workspace-summary-bar');
		await user.click(bar);

		const sheet = await screen.findByRole('dialog', { name: 'Your workspace' });
		expect(bar).toHaveAttribute('aria-expanded', 'true');
		expect(bar.getAttribute('aria-controls')).toBe(
			screen.getByTestId('workspace-summary-sheet').id,
		);
		// The shared panel body: newest API first, the list filter, and the
		// footer's import action — no "open the full workspace" any more.
		const apis = screen.getAllByTestId('workspace-panel-api');
		expect(apis.map((a) => a.textContent)).toEqual([
			expect.stringContaining('Slack'),
			expect.stringContaining('Stripe'),
		]);
		expect(screen.getByLabelText('Filter your APIs')).toBeInTheDocument();
		expect(screen.getByTestId('workspace-panel-import-own')).toBeInTheDocument();
		expect(screen.queryByLabelText('Open the full workspace')).not.toBeInTheDocument();
		expect(sheet).toBeInTheDocument();
		await checkA11y(document.body, { modal: true });

		await user.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
		await waitFor(() => expect(bar).toHaveFocus());
		expect(bar).toHaveAttribute('aria-expanded', 'false');
	});

	it('closes the sheet when a link inside it is followed', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestWith()}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		await user.click(screen.getByTestId('workspace-summary-bar'));
		// A row opens that API's hub.
		const [first] = await screen.findAllByTestId('workspace-panel-api');
		expect(first).toHaveAttribute('href', '/library/workspace/slack/slack-api/1');
		await user.click(first);
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});

	it('opens on a filtered workspace link and the sheet body narrows the list', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestWith()}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
			{ route: '/library?q=stri' },
		);
		// `?q=` is a request to see the list: the sheet opens on it, filtered.
		await screen.findByRole('dialog', { name: 'Your workspace' });
		const scroll = screen.getByTestId('workspace-panel-scroll');
		expect(scroll.className).toMatch(/overflow-y-auto/);
		expect(screen.getByLabelText('Filter your APIs')).toHaveValue('stri');
		expect(screen.getAllByTestId('workspace-panel-api')).toHaveLength(1);
		await user.clear(screen.getByLabelText('Filter your APIs'));
		expect(screen.getAllByTestId('workspace-panel-api')).toHaveLength(2);
	});

	it('"Import your own API" closes the sheet and hands over to the import dialog', async () => {
		const user = userEvent.setup();
		const onImportOwn = vi.fn();
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestWith()}
				pendingImports={[]}
				onImportOwn={onImportOwn}
			/>,
		);
		await user.click(screen.getByTestId('workspace-summary-bar'));
		await user.click(await screen.findByRole('button', { name: /import your own api/i }));
		expect(onImportOwn).toHaveBeenCalledOnce();
		await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
	});
});
