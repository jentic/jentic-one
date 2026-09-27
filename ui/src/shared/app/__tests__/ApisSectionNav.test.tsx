import { describe, it, expect } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { ApisSectionNav } from '@/shared/app';

describe('ApisSectionNav', () => {
	it('links the two halves of the APIs surface', () => {
		renderWithProviders(<ApisSectionNav />, { route: '/workspace' });
		const nav = screen.getByRole('navigation', { name: 'APIs' });
		expect(screen.getByRole('link', { name: 'Workspace' })).toHaveAttribute(
			'href',
			'/workspace',
		);
		expect(screen.getByRole('link', { name: 'Catalog' })).toHaveAttribute('href', '/discover');
		expect(nav).toBeInTheDocument();
	});

	it('marks the current half', () => {
		renderWithProviders(<ApisSectionNav />, { route: '/discover' });
		expect(screen.getByRole('link', { name: 'Catalog' })).toHaveAttribute(
			'aria-current',
			'page',
		);
		expect(screen.getByRole('link', { name: 'Workspace' })).not.toHaveAttribute('aria-current');
	});

	it('lights neither tab on an API detail page, which has its own back link', () => {
		renderWithProviders(<ApisSectionNav />, { route: '/workspace/stripe/stripe-api/1' });
		expect(screen.getByRole('link', { name: 'Workspace' })).not.toHaveAttribute('aria-current');
	});
});
