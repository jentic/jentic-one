/**
 * CredentialVersionScope — the one "Use for" picker every add-credential flow
 * renders: hidden without a version to pin, the version as its pin, and an
 * accessible name that stays stable across hosts.
 */
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, within } from '@/__tests__/test-utils';
import {
	CredentialVersionScope,
	pinnableVersionOf,
} from '@/shared/credentials/components/CredentialVersionScope';

describe('CredentialVersionScope', () => {
	it('labels the group and its two options', () => {
		renderWithProviders(<CredentialVersionScope version="v1" value="any" onChange={vi.fn()} />);
		const group = screen.getByRole('group', { name: 'Use this credential for' });
		expect(within(group).getByRole('button', { name: 'Any version' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(within(group).getByRole('button', { name: 'Version v1' })).toHaveAttribute(
			'aria-pressed',
			'false',
		);
		expect(screen.getByText('Use for')).toBeVisible();
	});

	it('reports the chosen scope', async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();
		renderWithProviders(
			<CredentialVersionScope version="v1" value="any" onChange={onChange} />,
		);
		await user.click(screen.getByRole('button', { name: 'Version v1' }));
		expect(onChange).toHaveBeenCalledWith('pinned');
	});

	it('notes that an unpinned credential reaches future versions', () => {
		renderWithProviders(<CredentialVersionScope version="v1" value="any" onChange={vi.fn()} />);
		expect(screen.getByTestId('credential-version-any-note')).toHaveTextContent(
			/apply to future\s+versions/i,
		);
	});

	it('drops the note once a version is pinned', () => {
		renderWithProviders(
			<CredentialVersionScope version="v1" value="pinned" onChange={vi.fn()} />,
		);
		expect(screen.queryByTestId('credential-version-any-note')).not.toBeInTheDocument();
	});

	it('renders nothing without a version to pin', () => {
		renderWithProviders(<CredentialVersionScope version="" value="any" onChange={vi.fn()} />);
		expect(
			screen.queryByRole('group', { name: 'Use this credential for' }),
		).not.toBeInTheDocument();
	});

	it('pins only a workspace API’s version, never a catalog placeholder', () => {
		expect(pinnableVersionOf({ source: 'local', version: ' 2.0.0 ' })).toBe('2.0.0');
		expect(pinnableVersionOf({ source: 'local', version: '' })).toBe('');
		expect(pinnableVersionOf({ source: 'catalog', version: '1.0.0' })).toBe('');
	});
});
