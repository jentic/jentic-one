import { describe, it, expect } from 'vitest';
import { useLocation } from 'react-router';
import { renderWithProviders, screen, userEvent, within, checkA11y } from '@/__tests__/test-utils';
import {
	WorkspaceApiCount,
	WorkspaceDockPanel,
	type WorkspaceDockPanelProps,
} from '@/modules/discover/components/WorkspaceDockPanel';
import type { WorkspaceDigest, WorkspaceDigestRow } from '@/modules/discover/api';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';

/**
 * The docked panel is the whole workspace view now: the FULL "Your APIs"
 * list (no cap, newest first), narrowed by a text filter and a serving-state
 * toggle mirrored in `?q=` / `?status=`; each row links to its hub; no
 * expand / "open your workspace" affordance.
 */

function LocationProbe() {
	const { pathname, search } = useLocation();
	return <div data-testid="location">{`${pathname}${search}`}</div>;
}

const day = (n: number) => `2026-01-${String(n).padStart(2, '0')}T00:00:00Z`;

function manyRows(): WorkspaceDigestRow[] {
	// 12 APIs (more than the old 8-row cap): one draft, one with an update,
	// one needing a credential.
	return Array.from({ length: 12 }, (_, i) =>
		makeDigestRow(`Api${String(i + 1).padStart(2, '0')}`, {
			createdAt: day(i + 1),
			...(i === 0 ? { currentRevisionId: null } : {}),
			...(i === 1 ? { updateAvailable: true, description: 'Payments and billing' } : {}),
			...(i === 2 ? { needsAuth: true, credentials: [], credentialCount: 0 } : {}),
		}),
	);
}

function digestWith(rows: WorkspaceDigestRow[], partial: Partial<WorkspaceDigest> = {}) {
	const draft = rows.filter((r) => r.currentRevisionId == null).length;
	return {
		rows,
		attention: [],
		attentionComplete: true,
		attentionSettled: true,
		byCatalogApiId: new Map(),
		totals: { apis: rows.length, live: rows.length - draft, draft },
		usageAvailable: false,
		usageExhaustive: false,
		credentialsError: false,
		isPending: false,
		error: null,
		complete: true,
		retry: () => {},
		...partial,
	} satisfies WorkspaceDigest;
}

function renderPanel(
	digest: WorkspaceDigest,
	route = '/library',
	extra: Partial<WorkspaceDockPanelProps> = {},
) {
	return renderWithProviders(
		<>
			<WorkspaceDockPanel
				digest={digest}
				pendingImports={[]}
				onImportOwn={() => {}}
				{...extra}
			/>
			<LocationProbe />
		</>,
		{ route },
	);
}

const titles = () => screen.getAllByTestId('workspace-panel-api').map((a) => a.textContent ?? '');

describe('WorkspaceDockPanel — full, filterable list', () => {
	it('lists every API, newest first, each row linking to its hub', () => {
		renderPanel(digestWith(manyRows()));
		const rows = screen.getAllByTestId('workspace-panel-api');
		expect(rows).toHaveLength(12);
		expect(titles()[0]).toContain('Api12');
		expect(titles()[11]).toContain('Api01');
		expect(rows[0]).toHaveAttribute('href', '/library/workspace/api12/api12-api/1');
		// No "+N more in your workspace", no expand, no "Open your workspace".
		expect(screen.queryByText(/more in your workspace/)).not.toBeInTheDocument();
		expect(screen.queryByLabelText('Open the full workspace')).not.toBeInTheDocument();
		expect(screen.queryByText('Open your workspace')).not.toBeInTheDocument();
		expect(screen.getByTestId('workspace-panel-import-own')).toHaveTextContent(
			'Import your own API',
		);
	});

	it('shows the one-line "No credential" hint on a row that needs one', () => {
		renderPanel(digestWith(manyRows()));
		const flagged = screen
			.getAllByTestId('workspace-panel-api')
			.filter((r) => within(r).queryByTestId('workspace-panel-api-no-credential'));
		expect(flagged.map((r) => r.textContent)).toEqual([expect.stringContaining('Api03')]);
	});

	it('filters by text (name / description) and mirrors it into ?q=', async () => {
		const user = userEvent.setup();
		renderPanel(digestWith(manyRows()));
		await user.type(screen.getByLabelText('Filter your APIs'), 'billing');
		expect(titles()).toEqual([expect.stringContaining('Api02')]);
		expect(screen.getByTestId('workspace-panel-filter-results')).toHaveTextContent('1 of 12');
		expect(screen.getByTestId('location')).toHaveTextContent('/library?q=billing');
	});

	it('filters by serving state with counts, seeded from ?status=', async () => {
		const user = userEvent.setup();
		renderPanel(digestWith(manyRows()), '/library?status=draft');
		expect(titles()).toEqual([expect.stringContaining('Api01')]);
		const toggle = screen.getByRole('group', { name: 'Filter by serving state' });
		expect(within(toggle).getByRole('button', { name: 'Live · 11' })).toBeInTheDocument();
		expect(within(toggle).getByRole('button', { name: 'Draft · 1' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		await user.click(within(toggle).getByRole('button', { name: 'Update available · 1' }));
		expect(titles()).toEqual([expect.stringContaining('Api02')]);
		expect(screen.getByTestId('location')).toHaveTextContent('/library?status=update');
		await user.click(within(toggle).getByRole('button', { name: 'All' }));
		expect(screen.getByTestId('location')).toHaveTextContent(/^\/library$/);
	});

	it('shows "No APIs match" with a Clear filter when nothing matches', async () => {
		const user = userEvent.setup();
		renderPanel(digestWith(manyRows()), '/library?q=zzz&status=live');
		expect(screen.getByTestId('workspace-panel-no-matches')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Clear filter' }));
		expect(screen.getAllByTestId('workspace-panel-api')).toHaveLength(12);
		expect(screen.getByTestId('location')).toHaveTextContent(/^\/library$/);
	});

	it('shows no counts while the list is still loading', () => {
		renderPanel(digestWith(manyRows(), { complete: false }));
		const toggle = screen.getByRole('group', { name: 'Filter by serving state' });
		expect(within(toggle).getByRole('button', { name: 'Live' })).toBeInTheDocument();
		expect(within(toggle).queryByRole('button', { name: /Live ·/ })).not.toBeInTheDocument();
	});

	it('the empty workspace shows its own empty state, not a filter', () => {
		renderPanel(digestWith([]));
		expect(screen.getByTestId('workspace-panel-empty')).toBeInTheDocument();
		expect(screen.queryByLabelText('Filter your APIs')).not.toBeInTheDocument();
	});

	it('keeps "Recent changes" collapsed under the list', () => {
		// No stream in this harness ⇒ no recent block at all (never an empty one).
		renderPanel(digestWith(manyRows()));
		expect(screen.queryByTestId('workspace-panel-recent')).not.toBeInTheDocument();
	});
});

describe('WorkspaceDockPanel — row meta (ops · credentials · agents)', () => {
	function single(extra: Partial<WorkspaceDigestRow>) {
		renderPanel(digestWith([makeDigestRow('Solo', extra)]));
		return screen.getByTestId('workspace-panel-api');
	}

	it('shows the credential count with an accessible name when some cover the API', () => {
		const creds = [
			makeMockCredential({ credential_id: 'c1' }),
			makeMockCredential({ credential_id: 'c2' }),
		];
		const row = single({ needsAuth: true, credentials: creds, credentialCount: 2 });
		const figure = within(row).getByTestId('workspace-panel-api-credentials');
		expect(figure).toHaveTextContent('2');
		expect(within(figure).getByText('2 credentials')).toHaveClass('sr-only');
		expect(
			within(row).queryByTestId('workspace-panel-api-no-credential'),
		).not.toBeInTheDocument();
	});

	it('says "1 credential" in the singular', () => {
		const row = single({ credentials: [makeMockCredential()], credentialCount: 1 });
		expect(within(row).getByText('1 credential')).toBeInTheDocument();
	});

	it('shows "No credential" when one is required and none covers it', () => {
		const row = single({ needsAuth: true, credentials: [], credentialCount: 0 });
		expect(within(row).getByTestId('workspace-panel-api-no-credential')).toHaveTextContent(
			'No credential',
		);
		expect(
			within(row).queryByTestId('workspace-panel-api-credentials'),
		).not.toBeInTheDocument();
	});

	it('shows nothing about credentials when none are required and none exist', () => {
		const row = single({ needsAuth: false, credentials: [], credentialCount: 0 });
		expect(
			within(row).queryByTestId('workspace-panel-api-credentials'),
		).not.toBeInTheDocument();
		expect(
			within(row).queryByTestId('workspace-panel-api-no-credential'),
		).not.toBeInTheDocument();
	});

	it('never shows a "0" (or "No credential") while credentials are still loading', () => {
		const row = single({ needsAuth: true, credentials: null, credentialCount: null });
		expect(
			within(row).queryByTestId('workspace-panel-api-credentials'),
		).not.toBeInTheDocument();
		expect(
			within(row).queryByTestId('workspace-panel-api-no-credential'),
		).not.toBeInTheDocument();
	});

	it('shows the operation count with an accessible name, in agents · ops · creds order', async () => {
		const row = single({
			operationCount: 21,
			credentials: [makeMockCredential()],
			credentialCount: 1,
		});
		const ops = within(row).getByTestId('workspace-panel-api-ops');
		expect(ops).toHaveTextContent('21');
		expect(within(ops).getByText('21 operations')).toHaveClass('sr-only');
		const creds = within(row).getByTestId('workspace-panel-api-credentials');
		// Bound agents arrive from the (mocked) per-credential read.
		const agents = await within(row).findByTestId('workspace-panel-api-agents');
		const follows = (a: Node, b: Node) =>
			(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
		// Credentials last, so a missing / amber one leaves the rest aligned.
		expect(follows(agents, ops)).toBe(true);
		expect(follows(ops, creds)).toBe(true);
	});

	it('shows a draft API’s reported operation count too', () => {
		const row = single({ currentRevisionId: null, operationCount: 1 });
		expect(within(row).getByText('1 operation')).toBeInTheDocument();
	});
});

describe('WorkspaceDockPanel — serving state, usage, pending imports', () => {
	it('names the serving state as a plain word, and appends "Update available"', () => {
		renderPanel(digestWith([makeDigestRow('Solo', { updateAvailable: true })]));
		const row = screen.getByTestId('workspace-panel-api');
		expect(within(row).getByTestId('api-state-live')).toHaveTextContent(/^Live$/);
		expect(within(row).getByTestId('api-state-update')).toHaveTextContent('Update available');
	});

	it('shows 7-day calls and failures in the right column, with no sparkline', () => {
		renderPanel(
			digestWith(
				[
					makeDigestRow('Solo', {
						usage: {
							total: 47,
							failed: 8,
							trend: [1, 2, 3],
						} as WorkspaceDigestRow['usage'],
					}),
				],
				{ usageAvailable: true },
			),
		);
		const row = screen.getByTestId('workspace-panel-api');
		expect(row).toHaveTextContent('47 calls');
		expect(within(row).getByTestId('workspace-panel-api-failed')).toHaveTextContent('8 failed');
		// The sparkline was dropped: no chart svg in the row (lucide icons only).
		expect(row.querySelector('svg.overflow-visible')).toBeNull();
	});

	it('lists in-flight imports at the top of the list as "Adding…" rows', () => {
		renderWithProviders(
			<WorkspaceDockPanel
				digest={digestWith(manyRows())}
				pendingImports={[{ apiId: 'petstore', label: 'Petstore' }]}
				onImportOwn={() => {}}
			/>,
		);
		const pending = screen.getByTestId('workspace-panel-importing');
		expect(pending).toHaveTextContent(/Petstore.*Adding…/);
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderPanel(digestWith(manyRows()));
		await checkA11y(container);
	});
});

describe('WorkspaceDockPanel — drag-to-add', () => {
	it('is idle with no drop slot when nothing is dragged', () => {
		renderPanel(digestWith(manyRows()), '/library', { drop: null });
		expect(screen.getByTestId('workspace-dock-panel')).toHaveAttribute('data-drag', 'idle');
		expect(screen.queryByTestId('workspace-drop-slot')).not.toBeInTheDocument();
	});

	it('shows the drop slot with the dragged name while dragging', () => {
		renderPanel(digestWith(manyRows()), '/library', {
			drop: { phase: 'dragging', name: 'Petstore' },
		});
		expect(screen.getByTestId('workspace-dock-panel')).toHaveAttribute('data-drag', 'dragging');
		const slot = screen.getByTestId('workspace-drop-slot');
		expect(slot).toHaveTextContent('Drop to add Petstore');
		expect(slot).not.toHaveAttribute('data-over');
		// The slot sits above the first API row.
		const first = screen.getAllByTestId('workspace-panel-api')[0];
		expect(slot.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('marks the slot and the panel when the drag is over it', () => {
		renderPanel(digestWith(manyRows()), '/library', {
			drop: { phase: 'over', name: 'Petstore' },
		});
		expect(screen.getByTestId('workspace-dock-panel')).toHaveAttribute('data-drag', 'over');
		expect(screen.getByTestId('workspace-drop-slot')).toHaveAttribute('data-over', 'true');
	});

	it('shows the drop slot on an empty workspace too', () => {
		renderPanel(digestWith([]), '/library', { drop: { phase: 'dragging', name: 'Petstore' } });
		expect(screen.getByTestId('workspace-drop-slot')).toBeInTheDocument();
		expect(screen.getByTestId('workspace-panel-empty')).toBeInTheDocument();
	});

	it('flashes the rows whose catalog api id just landed', () => {
		const rows = manyRows().map((r, i) => (i === 4 ? { ...r, catalogApiId: 'cat_5' } : r));
		renderPanel(digestWith(rows), '/library', { justAddedApiIds: new Set(['cat_5']) });
		const flashed = screen
			.getAllByTestId('workspace-panel-api')
			.filter((a) => a.hasAttribute('data-just-added'));
		expect(flashed).toHaveLength(1);
		expect(flashed[0]).toHaveTextContent('Api05');
		expect(flashed[0]).toHaveClass('animate-flash-added');
	});
});

describe('WorkspaceApiCount', () => {
	it('folds live / draft totals into the header line', () => {
		renderWithProviders(<WorkspaceApiCount digest={digestWith(manyRows())} />);
		expect(screen.getByTestId('workspace-api-count')).toHaveTextContent(
			'12 APIs · 11 live · 1 draft',
		);
	});

	it('says only "N APIs" when every API is live', () => {
		const rows = manyRows().map((r) => ({ ...r, currentRevisionId: 'rev' }));
		renderWithProviders(<WorkspaceApiCount digest={digestWith(rows)} />);
		expect(screen.getByTestId('workspace-api-count')).toHaveTextContent(/^12 APIs$/);
	});

	it('says nothing until the list is whole', () => {
		renderWithProviders(
			<WorkspaceApiCount digest={digestWith(manyRows(), { complete: false })} />,
		);
		expect(screen.queryByTestId('workspace-api-count')).not.toBeInTheDocument();
	});
});
