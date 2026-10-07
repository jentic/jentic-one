import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { ApiError } from '@/shared/api';

describe('ErrorAlert', () => {
	it('renders a string message in an alert region', () => {
		renderWithProviders(<ErrorAlert message="Something failed" />);
		expect(screen.getByRole('alert')).toHaveTextContent('Something failed');
	});

	it('renders an Error instance message', () => {
		renderWithProviders(<ErrorAlert message={new Error('Boom')} />);
		expect(screen.getByRole('alert')).toHaveTextContent('Boom');
	});

	it('renders an optional title above the message', () => {
		renderWithProviders(<ErrorAlert title="Couldn't load" message="Boom" />);
		expect(screen.getByRole('alert')).toHaveTextContent("Couldn't loadBoom");
	});

	it('prefers the RFC 9457 problem detail over the status text', () => {
		const err = new ApiError(
			{ method: 'POST', url: '/credentials' },
			{
				url: '/credentials',
				ok: false,
				status: 400,
				statusText: 'Bad Request',
				body: {
					type: 'invalid_credential_input',
					status: 400,
					title: 'Bad Request',
					detail: "api.name 'a.com/b' is not an identity — it looks like a spec path",
				},
			},
			'Bad Request',
		);
		renderWithProviders(<ErrorAlert message={err} />);
		expect(screen.getByRole('alert')).toHaveTextContent(
			"api.name 'a.com/b' is not an identity — it looks like a spec path",
		);
	});

	it('keeps the status text when the body carries no string detail (422 field list)', () => {
		const err = new ApiError(
			{ method: 'POST', url: '/x' },
			{
				url: '/x',
				ok: false,
				status: 422,
				statusText: 'Unprocessable Entity',
				body: { detail: [{ loc: ['body', 'name'], msg: 'required' }] },
			},
			'Unprocessable Entity',
		);
		renderWithProviders(<ErrorAlert message={err} />);
		expect(screen.getByRole('alert')).toHaveTextContent('Unprocessable Entity');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<ErrorAlert message="Network error" />);
		await checkA11y(container);
	});
});
