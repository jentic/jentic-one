import { render, screen } from '@testing-library/react';
import axe from 'axe-core';
import { MemoryRouter } from 'react-router-dom';
import { BackButton } from '@/components/ui/BackButton';

describe('BackButton', () => {
	it('renders as a button by default (history-aware mode)', () => {
		render(
			<MemoryRouter>
				<BackButton to="/dashboard" label="Back to dashboard" />
			</MemoryRouter>,
		);
		const btn = screen.getByRole('button', { name: /Back to dashboard/i });
		expect(btn).toBeInTheDocument();
	});

	it('renders as a link when useHistory is false', () => {
		render(
			<MemoryRouter>
				<BackButton to="/dashboard" label="Back to dashboard" useHistory={false} />
			</MemoryRouter>,
		);
		const link = screen.getByRole('link', { name: /Back to dashboard/i });
		expect(link).toHaveAttribute('href', '/dashboard');
	});

	it('has no accessibility violations', async () => {
		const { container } = render(
			<MemoryRouter>
				<BackButton to="/home" label="Back to Home" />
			</MemoryRouter>,
		);
		const results = await axe.run(container);
		expect(results.violations).toEqual([]);
	});
});
