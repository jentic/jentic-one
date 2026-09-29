import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import {
	useWorkspaceDigest,
	type AttentionEntry,
	type WorkspaceDigest,
} from '@/modules/discover/api';
import { WorkspacePanelBody } from '@/modules/discover/components/WorkspaceDockPanel';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';

/**
 * The docked panel's "Needs attention" block: which rules put an API there
 * (from real registry / credential / overlay / usage reads) and where each
 * link lands — the hub (on the right tab, straight onto Add credential for a
 * missing credential), or the Workspace view pre-filtered by `?status=`.
 */

function api(
	vendor: string,
	fields: {
		live?: boolean;
		update?: boolean;
		auth?: boolean;
	} = {},
) {
	return {
		api: { vendor, name: `${vendor}-api`, version: '1', host: null },
		display_name: vendor[0].toUpperCase() + vendor.slice(1),
		description: null,
		icon_url: null,
		catalog_api_id: null,
		current_revision_id: fields.live === false ? null : `rev_${vendor}`,
		revision_count: 1,
		operation_count: 2,
		security_schemes: fields.auth ? ['bearer'] : [],
		update_available: fields.update ?? false,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
		_links: { self: `/apis/${vendor}/${vendor}-api/1` },
	};
}

function DigestProbe() {
	const digest = useWorkspaceDigest();
	if (!digest.attentionComplete) return <p>loading</p>;
	return (
		<ul>
			{digest.attention.map((a) => (
				<li key={a.id} data-testid={`rule-${a.id}`}>
					{a.rows.map((r) => r.title).join(',')}
					{a.atLeast ? '+' : ''}
				</li>
			))}
		</ul>
	);
}

describe('useWorkspaceDigest attention rules', () => {
	beforeEach(() => {
		setToken('test-token');
		worker.use(
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						api('stripe', { update: true, auth: true }),
						api('draft', { live: false }),
						api('locked', { auth: true }),
						api('open'),
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/credentials', () =>
				HttpResponse.json({
					data: [
						makeMockCredential({
							credential_id: 'stripe_key',
							api: { vendor: 'stripe', name: 'stripe-api', version: '' },
						}),
						// An inactive credential never satisfies "has a credential".
						makeMockCredential({
							credential_id: 'locked_off',
							api: { vendor: 'locked', name: 'locked-api', version: '1' },
							active: false,
						}),
					],
					has_more: false,
					next_cursor: null,
				}),
			),
			http.get('/apis/:vendor/:name/:version/overlays', ({ params }) =>
				HttpResponse.json({
					data: params.vendor === 'open' ? [{ id: 'ov_1', status: 'pending' }] : [],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
	});

	it('flags updates, pending overlays, missing credentials and drafts from real reads', async () => {
		renderWithProviders(<DigestProbe />);
		expect(await screen.findByTestId('rule-updates')).toHaveTextContent('Stripe');
		expect(screen.getByTestId('rule-overlays')).toHaveTextContent('Open');
		// Needs auth + only an inactive credential → missing; Stripe's wildcard key covers it.
		expect(screen.getByTestId('rule-credentials')).toHaveTextContent(/^Locked$/);
		expect(screen.getByTestId('rule-drafts')).toHaveTextContent('Draft');
		// Usage is admin-only; the test user can't read it, so no failure rule is claimed.
		expect(screen.queryByTestId('rule-failures')).not.toBeInTheDocument();
	});
});

function digestWith(attention: AttentionEntry[]): WorkspaceDigest {
	return {
		rows: attention.flatMap((a) => a.rows),
		attention,
		attentionComplete: true,
		attentionSettled: true,
		byCatalogApiId: new Map(),
		totals: { apis: 0, live: 0, draft: 0 },
		usageAvailable: false,
		usageExhaustive: false,
		credentialsError: false,
		isPending: false,
		error: null,
		complete: true,
		retry: () => {},
	};
}

describe('WorkspacePanelBody attention links', () => {
	const rows = ['Alpha', 'Beta', 'Gamma', 'Delta'].map((t) => makeDigestRow(t));

	it('opens a missing-credential API straight on its hub Add credential form', () => {
		renderWithProviders(
			<WorkspacePanelBody
				digest={digestWith([
					{
						id: 'credentials',
						label: 'no credential yet',
						rows: [rows[0]],
						tab: 'overview',
					},
				])}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		const item = screen.getByTestId('attention-credentials');
		expect(within(item).getByRole('link', { name: 'Alpha' })).toHaveAttribute(
			'href',
			'/library/workspace/alpha/alpha-api/1?credential=new',
		);
	});

	it('links overlays to the hub Versions tab and "+N more" drafts to ?status=draft', () => {
		renderWithProviders(
			<WorkspacePanelBody
				digest={digestWith([
					{
						id: 'overlays',
						label: 'overlay awaiting review',
						rows: [rows[1]],
						tab: 'versions',
					},
					{ id: 'drafts', label: 'draft only', rows, tab: 'versions' },
				])}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		expect(
			within(screen.getByTestId('attention-overlays')).getByRole('link', { name: 'Beta' }),
		).toHaveAttribute('href', '/library/workspace/beta/beta-api/1?tab=versions');
		const drafts = screen.getByTestId('attention-drafts');
		expect(within(drafts).getByRole('link', { name: '+1 more' })).toHaveAttribute(
			'href',
			'/library/workspace?status=draft',
		);
	});

	it('shows neither "All good" nor a skeleton when a source failed (settled, not complete)', async () => {
		renderWithProviders(
			<WorkspacePanelBody
				digest={{ ...digestWith([]), attentionComplete: false, attentionSettled: true }}
				pendingImports={[]}
				onImportOwn={() => {}}
			/>,
		);
		await waitFor(() =>
			expect(screen.queryByTestId('workspace-panel-all-good')).not.toBeInTheDocument(),
		);
		expect(screen.queryByTestId('workspace-panel-attention')).not.toBeInTheDocument();
	});
});
