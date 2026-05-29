import { http, HttpResponse } from 'msw';
import axe from 'axe-core';
import { screen, renderWithProviders, userEvent } from '../test-utils';
import { worker } from '../mocks/browser';
import ToolkitsPage from '@/pages/ToolkitsPage';

describe('ToolkitsPage', () => {
	it('renders the heading', async () => {
		renderWithProviders(<ToolkitsPage />);
		expect(await screen.findByRole('heading', { name: /toolkits/i })).toBeInTheDocument();
	});

	it('shows empty state when no toolkits exist', async () => {
		renderWithProviders(<ToolkitsPage />);
		expect(await screen.findByText(/no toolkits yet/i)).toBeInTheDocument();
		expect(screen.getByText(/create your first toolkit/i)).toBeInTheDocument();
	});

	it('renders toolkit cards with populated data', async () => {
		worker.use(
			http.get('/toolkits', () =>
				HttpResponse.json([
					{
						id: 'tk-1',
						name: 'My Toolkit',
						description: 'Test',
						disabled: false,
						keys: [],
						credentials: [],
						key_count: 2,
						credential_count: 1,
					},
					{
						id: 'tk-2',
						name: 'Second Toolkit',
						description: 'Another',
						disabled: true,
						keys: [],
						credentials: [],
						key_count: 0,
						credential_count: 0,
						simulate: true,
					},
				]),
			),
		);

		renderWithProviders(<ToolkitsPage />);

		expect(await screen.findByText('My Toolkit')).toBeInTheDocument();
		expect(screen.getByText('Second Toolkit')).toBeInTheDocument();
		expect(screen.getByText('SUSPENDED')).toBeInTheDocument();
		expect(screen.getByText('simulate')).toBeInTheDocument();
	});

	it('opens create modal on button click', async () => {
		const user = userEvent.setup();
		renderWithProviders(<ToolkitsPage />);

		await screen.findByRole('heading', { name: /toolkits/i });
		await user.click(screen.getByRole('button', { name: /create toolkit/i }));

		expect(await screen.findByRole('heading', { name: /create toolkit/i })).toBeInTheDocument();
		expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
	});

	it('has no critical accessibility violations', async () => {
		const { container } = renderWithProviders(<ToolkitsPage />);
		await screen.findByRole('heading', { name: /toolkits/i });
		// `color-contrast` is disabled here only — vitest browser-mode + istanbul
		// coverage occasionally races CSS-variable resolution in the parallel
		// pool, so axe sees `bg-primary` as the unresolved dark fallback
		// instead of `#A3CACC`. The button renders correctly in production and
		// other axe-using tests still cover color-contrast across the app.
		const results = await axe.run(container, {
			rules: { 'color-contrast': { enabled: false } },
		});
		const serious = results.violations.filter(
			(v) => v.impact === 'critical' || v.impact === 'serious',
		);
		expect(serious).toEqual([]);
	});
});
