import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { Disclosure } from '@/shared/ui/Disclosure';

describe('Disclosure', () => {
	it('hides the body until the summary is clicked', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Disclosure summary="Which URLs are allowed?">
				<p>Only http(s) URLs.</p>
			</Disclosure>,
		);

		// Body is present in the DOM but the section starts collapsed.
		const details = screen.getByText('Which URLs are allowed?').closest('details');
		expect(details).not.toBeNull();
		expect(details).not.toHaveAttribute('open');

		await user.click(screen.getByText('Which URLs are allowed?'));
		expect(details).toHaveAttribute('open');
	});

	it('respects defaultOpen', () => {
		renderWithProviders(
			<Disclosure summary="Details" defaultOpen>
				<p>Body</p>
			</Disclosure>,
		);
		const details = screen.getByText('Details').closest('details');
		expect(details).toHaveAttribute('open');
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<Disclosure summary="More">
				<p>Body</p>
			</Disclosure>,
		);
		await checkA11y(container);
	});
});
