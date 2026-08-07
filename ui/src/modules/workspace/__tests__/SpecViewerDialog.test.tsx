import { describe, it, expect, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import userEvent from '@testing-library/user-event';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { SpecViewerDialog } from '@/modules/workspace/components/SpecViewerDialog';
import type { ApiKey } from '@/modules/workspace/api';

const KEY: ApiKey = { vendor: 'stripe.com', name: 'stripe-api', version: '1' };

const TARGET_SPEC = {
	openapi: '3.1.0',
	info: { title: 'Pets', version: '2' },
	servers: [{ url: 'https://eu.example' }, { url: 'https://us.example' }],
};
const BASE_SPEC = {
	openapi: '3.1.0',
	info: { title: 'Pets', version: '2' },
	servers: [{ url: 'https://us.example' }],
};

function mockSpecs() {
	worker.use(
		http.get('/apis/:vendor/:name/:version/openapi', () => HttpResponse.json(TARGET_SPEC)),
		http.get('/apis/:vendor/:name/:version/revisions/:revisionId/openapi', ({ params }) =>
			HttpResponse.json(params.revisionId === 'rev_base' ? BASE_SPEC : TARGET_SPEC),
		),
	);
}

const DIFF_BASE = { revisionId: 'rev_base', label: 'previous · rev_base' };

describe('SpecViewerDialog', () => {
	afterEach(() => {
		worker.resetHandlers();
	});

	it('opens in diff mode with proper tab semantics and shows only the changed sections', async () => {
		mockSpecs();
		renderWithProviders(
			<SpecViewerDialog apiKey={KEY} open onClose={() => {}} diffAgainst={DIFF_BASE} />,
		);

		// The Diff/Full-spec switcher is a real tablist, not two bare buttons.
		const tablist = await screen.findByRole('tablist', { name: 'Spec view' });
		expect(tablist).toBeVisible();
		const diffTab = screen.getByRole('tab', { name: /Diff vs previous · rev_base/ });
		expect(diffTab).toHaveAttribute('aria-selected', 'true');

		// One structural entry: the servers block. Nothing else is dumped.
		await waitFor(() => expect(screen.getAllByTestId('spec-diff-entry')).toHaveLength(1));
		expect(screen.getByText('$.servers')).toBeInTheDocument();
		// Before/After direction is announced as text, not color alone.
		expect(screen.getByLabelText('Before, at $.servers')).toBeInTheDocument();
		expect(screen.getByLabelText('After, at $.servers')).toBeInTheDocument();
		expect(screen.queryByTestId('spec-viewer-content')).not.toBeInTheDocument();
	});

	it('toggles to the full spec', async () => {
		mockSpecs();
		renderWithProviders(
			<SpecViewerDialog apiKey={KEY} open onClose={() => {}} diffAgainst={DIFF_BASE} />,
		);

		await screen.findByRole('tablist', { name: 'Spec view' });
		await userEvent.click(screen.getByRole('tab', { name: 'Full spec' }));

		const full = await screen.findByTestId('spec-viewer-content');
		expect(full).toHaveTextContent('https://eu.example');
	});

	it('honors defaultMode="full" (header "View spec" keeps its label promise)', async () => {
		mockSpecs();
		renderWithProviders(
			<SpecViewerDialog
				apiKey={KEY}
				open
				onClose={() => {}}
				diffAgainst={DIFF_BASE}
				defaultMode="full"
			/>,
		);

		expect(await screen.findByTestId('spec-viewer-content')).toBeVisible();
		// The Diff tab is still available for opting in.
		expect(screen.getByRole('tab', { name: /Diff vs/ })).toBeInTheDocument();
	});

	it('shows only the full spec (no toggle) without a diff base', async () => {
		mockSpecs();
		renderWithProviders(<SpecViewerDialog apiKey={KEY} open onClose={() => {}} />);

		expect(await screen.findByTestId('spec-viewer-content')).toBeVisible();
		expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
	});

	it('reports "No differences" when base and target match', async () => {
		worker.use(
			http.get('/apis/:vendor/:name/:version/openapi', () => HttpResponse.json(BASE_SPEC)),
			http.get('/apis/:vendor/:name/:version/revisions/:revisionId/openapi', () =>
				HttpResponse.json(BASE_SPEC),
			),
		);
		renderWithProviders(
			<SpecViewerDialog apiKey={KEY} open onClose={() => {}} diffAgainst={DIFF_BASE} />,
		);

		expect(await screen.findByTestId('spec-diff-empty')).toHaveTextContent(
			'No differences vs previous · rev_base.',
		);
	});
});
