/**
 * The workspace panel's "no credential yet" item adds the credential IN PLACE:
 * an API name is a button that opens the shared Add credential flow on that
 * API's form (step 2, seeded like the API hub), and a usable credential is
 * confirmed in the panel — the API then drops off the list once the
 * credentials slice refetches. A discarded flow confirms nothing. The mobile
 * sheet hands over to the same flow (closing itself first).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
	checkA11y,
	renderWithProviders,
	screen,
	userEvent,
	waitFor,
	within,
} from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { worker } from '@/mocks/browser';
import { Toaster } from '@/shared/ui';
import {
	makeMockApi,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { useWorkspaceDigest, type WorkspaceDigest } from '@/modules/discover/api';
import { WorkspacePanelBody } from '@/modules/discover/components/WorkspaceDockPanel';
import { WorkspaceSummaryBar } from '@/modules/discover/components/WorkspaceSummaryBar';
import { usePanelCredentialFlow } from '@/modules/discover/components/usePanelCredentialFlow';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';

const HIRES = makeMockApi({
	vendor: '100hires.com',
	name: '100hires-com',
	version: '2.0.0',
	displayName: '100hires.com',
	catalogApiId: '100hires.com',
});

/** What LibraryPage wires: the real digest, the in-place flow, the panel body. */
function Harness({ noticeMs }: { noticeMs?: number }) {
	const digest = useWorkspaceDigest();
	const flow = usePanelCredentialFlow({ noticeMs });
	return (
		<>
			<WorkspacePanelBody
				digest={digest}
				pendingImports={[]}
				onImportOwn={() => {}}
				onAddCredential={flow.addCredentialFor}
				credentialNotice={flow.notice}
				onDismissCredentialNotice={flow.dismissNotice}
			/>
			{flow.element}
			<Toaster />
		</>
	);
}

async function openFromAttention(user: ReturnType<typeof userEvent.setup>) {
	const item = await screen.findByTestId('attention-credentials');
	const button = within(item).getByRole('button', {
		name: 'Add a credential for 100hires.com',
	});
	// A button (opens in place), not a link to the hub.
	expect(within(item).queryByRole('link', { name: '100hires.com' })).not.toBeInTheDocument();
	await user.click(button);
	return screen.findByRole('dialog', { name: 'Add credential — 100hires.com' });
}

describe('workspace panel — add a credential in place', () => {
	beforeEach(() => {
		setToken('test-token');
		resetApisStore([HIRES]);
		resetCredentialsStore([]);
	});
	afterEach(() => {
		worker.events.removeAllListeners();
		resetApisStore();
		resetCredentialsStore();
	});

	it('opens the shared flow at step 2 on the clicked API', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);

		const flow = await openFromAttention(user);
		expect(within(flow).getByText(/Step 2 of 2/)).toBeVisible();
		expect(await within(flow).findByTestId('selected-api-summary')).toHaveTextContent(
			'100hires.com/100hires-com',
		);
		expect(within(flow).getByLabelText(/^Name/)).toHaveValue('100hires.com');
		// Back to the picker stays available (initialApi, not pinned).
		expect(within(flow).getByRole('button', { name: 'Back' })).toBeVisible();
	});

	it('confirms a created credential in the panel and drops the API from the list', async () => {
		const user = userEvent.setup();
		const createdApis: unknown[] = [];
		worker.events.on('request:start', ({ request }) => {
			if (request.method === 'POST' && new URL(request.url).pathname === '/credentials')
				void request
					.clone()
					.json()
					.then((b: { api?: unknown }) => createdApis.push(b.api));
		});
		renderWithProviders(<Harness />);

		const flow = await openFromAttention(user);
		// Opened from this API, so the credential defaults to its version.
		expect(await within(flow).findByTestId('selected-api-summary')).toHaveTextContent(
			'100hires.com/100hires-com@2.0.0',
		);
		await user.type(await within(flow).findByLabelText(/^API key/), 'sk_test_123');
		await user.click(within(flow).getByRole('button', { name: 'Create credential' }));

		const notice = await screen.findByTestId('workspace-panel-credential-added');
		expect(notice).toHaveTextContent('Credential added for 100hires.com');
		expect(createdApis).toEqual([
			{
				vendor: '100hires.com',
				name: '100hires-com',
				version: '2.0.0',
				catalog_api_id: '100hires.com',
			},
		]);
		expect(notice.closest('[role="status"]')).not.toBeNull();
		// The flow closed; its own toast still fires.
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: /Add credential/ }),
			).not.toBeInTheDocument(),
		);
		expect(await screen.findByText('Credential created')).toBeInTheDocument();
		// The create invalidated the credentials slice: nothing is missing now.
		await waitFor(() =>
			expect(screen.queryByTestId('attention-credentials')).not.toBeInTheDocument(),
		);
		await checkA11y(document.body);

		await user.click(screen.getByTestId('workspace-panel-credential-added-dismiss'));
		expect(screen.queryByTestId('workspace-panel-credential-added')).not.toBeInTheDocument();
	});

	it('auto-dismisses the confirmation', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness noticeMs={400} />);

		const flow = await openFromAttention(user);
		await user.type(await within(flow).findByLabelText(/^API key/), 'sk_test_123');
		await user.click(within(flow).getByRole('button', { name: 'Create credential' }));

		expect(await screen.findByTestId('workspace-panel-credential-added')).toBeVisible();
		await waitFor(() =>
			expect(
				screen.queryByTestId('workspace-panel-credential-added'),
			).not.toBeInTheDocument(),
		);
	});

	it('shows no confirmation when the flow is discarded', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Harness />);

		const flow = await openFromAttention(user);
		await user.click(within(flow).getByRole('button', { name: 'Cancel' }));
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: /Add credential/ }),
			).not.toBeInTheDocument(),
		);
		expect(screen.queryByTestId('workspace-panel-credential-added')).not.toBeInTheDocument();
		expect(screen.getByTestId('attention-credentials')).toBeInTheDocument();
	});
});

function digestOf(rows: WorkspaceDigest['rows']): WorkspaceDigest {
	return {
		rows,
		attention: [{ id: 'credentials', label: 'no credential yet', rows, tab: 'overview' }],
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
	};
}

describe('WorkspaceSummaryBar — add a credential handoff', () => {
	it('closes the sheet, then hands the API to the in-place flow', async () => {
		const user = userEvent.setup();
		const onAddCredential = vi.fn();
		const row = makeDigestRow('Alpha', { needsAuth: true, securitySchemes: ['apiKey'] });
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestOf([row])}
				pendingImports={[]}
				onImportOwn={() => {}}
				onAddCredential={onAddCredential}
			/>,
		);
		await user.click(screen.getByTestId('workspace-summary-bar'));
		const sheet = await screen.findByRole('dialog', { name: 'Your workspace' });
		await user.click(within(sheet).getByRole('button', { name: 'Add a credential for Alpha' }));
		expect(onAddCredential).toHaveBeenCalledExactlyOnceWith(row);
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'Your workspace' }),
			).not.toBeInTheDocument(),
		);
	});

	it('shows the confirmation on the bar while the sheet is closed', () => {
		renderWithProviders(
			<WorkspaceSummaryBar
				digest={digestOf([])}
				pendingImports={[]}
				onImportOwn={() => {}}
				credentialNotice={{ id: 1, label: 'Alpha' }}
			/>,
		);
		expect(screen.getByTestId('workspace-summary-credential-added')).toHaveTextContent(
			'Credential added for Alpha',
		);
	});
});
