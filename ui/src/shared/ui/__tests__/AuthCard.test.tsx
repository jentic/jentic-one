import type { FormEvent } from 'react';
import { fireEvent, renderWithProviders, screen } from '@/__tests__/test-utils';
import { AuthCard } from '@/shared/ui/AuthCard';

describe('AuthCard', () => {
	it('renders a form card inside a centring <main> by default', () => {
		const onSubmit = vi.fn((e: FormEvent) => e.preventDefault());
		renderWithProviders(
			<AuthCard aria-label="Sign in" onSubmit={onSubmit}>
				<button type="submit">Go</button>
			</AuthCard>,
		);
		const form = screen.getByRole('form', { name: 'Sign in' });
		expect(form.parentElement?.tagName).toBe('MAIN');
		expect(form).toHaveClass('bg-surface-1', 'shadow-elevated', 'max-w-sm');
		fireEvent.click(screen.getByRole('button', { name: 'Go' }));
		expect(onSubmit).toHaveBeenCalledOnce();
	});

	it('renders a centred status card as a div', () => {
		renderWithProviders(
			<AuthCard as="div" centered role="status">
				Signing you in…
			</AuthCard>,
		);
		const card = screen.getByRole('status');
		expect(card.tagName).toBe('DIV');
		expect(card).toHaveClass('text-center', 'shadow-elevated');
	});

	it('carries an alert role through (the OAuth popup / SSO callback error cards)', () => {
		renderWithProviders(
			<AuthCard as="div" centered role="alert">
				Sign-in failed
			</AuthCard>,
		);
		expect(screen.getByRole('alert')).toHaveTextContent('Sign-in failed');
	});
});
