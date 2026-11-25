import { describe, expect, it } from 'vitest';
import { useState } from 'react';
import { renderWithProviders, screen, userEvent } from '@/__tests__/test-utils';
import { AccessEndFields } from '@/shared/credentials/components/AccessEndFields';
import { IdleLimitField } from '@/shared/credentials/components/IdleLimitField';

function Warning({ initiallyWarnable }: { initiallyWarnable: boolean }) {
	const [warnable, setWarnable] = useState(initiallyWarnable);
	return (
		<>
			<button type="button" onClick={() => setWarnable((w) => !w)}>
				flip
			</button>
			<AccessEndFields
				warn={false}
				onWarnChange={() => {}}
				warnable={warnable}
				afterExpiry="hold"
				onAfterExpiryChange={() => {}}
			/>
		</>
	);
}

describe('the warning before access ends', () => {
	it('is described by its hint', () => {
		renderWithProviders(<Warning initiallyWarnable />);

		const box = screen.getByRole('checkbox', { name: /Warn me before this access ends/ });
		const hint = screen.getByTestId('binding-idle-warning-hint');
		expect(box).toHaveAttribute('aria-describedby', hint.id);
		expect(hint.id).not.toBe('');
	});

	it('announces when it goes away, and says nothing while it is there', async () => {
		renderWithProviders(<Warning initiallyWarnable />);
		const status = screen.getByTestId('binding-idle-warning-status');
		expect(status).toHaveAttribute('role', 'status');
		expect(status).toHaveTextContent('');

		await userEvent.click(screen.getByRole('button', { name: 'flip' }));

		expect(screen.queryByRole('checkbox', { name: /Warn me/ })).toBeNull();
		expect(status).toHaveTextContent(/not available for access that ends in under 4 days/);
	});
});

describe('the idle limit under an org maximum', () => {
	it('says why it cannot be turned off', () => {
		renderWithProviders(
			<IdleLimitField
				value={{ kind: 'preset', preset: '14d' }}
				onChange={() => {}}
				maxSeconds={30 * 86_400}
			/>,
		);

		const box = screen.getByRole('checkbox', { name: /Also end if unused for/ });
		expect(box).toBeDisabled();
		const reason = screen.getByTestId('binding-idle-required');
		expect(reason).toHaveTextContent('Your org requires an idle limit of at most 30 days');
		expect(box).toHaveAttribute('aria-describedby', reason.id);
	});

	it('gives no reason when nothing requires it', () => {
		renderWithProviders(
			<IdleLimitField value={{ kind: 'preset', preset: '14d' }} onChange={() => {}} />,
		);

		expect(screen.queryByTestId('binding-idle-required')).toBeNull();
		expect(screen.getByRole('checkbox', { name: /Also end if unused for/ })).toBeEnabled();
	});
});
