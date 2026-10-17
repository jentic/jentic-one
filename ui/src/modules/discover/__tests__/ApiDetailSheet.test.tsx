/**
 * ApiDetailSheet's header line under the title: the domain, then the version.
 * A bare-domain entry (`adyen.com`) already prints that domain as its title and
 * api-id chip, so the subtitle keeps only the version rather than a third copy.
 */
import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import type { DiscoveryEntity } from '@/modules/discover/api';
import { ApiDetailSheet } from '@/modules/discover/components/ApiDetailSheet';

const ADYEN: DiscoveryEntity = {
	id: 'adyen.com',
	entryId: 'public/adyen.com',
	apiId: 'adyen.com',
	sourceSlug: 'public',
	browseKey: 'adyen.com',
	displayVendor: 'adyen.com',
	alternates: 0,
	hasSpec: true,
	summary: 'adyen.com',
	registered: false,
	updateAvailable: false,
	vendor: 'adyen.com',
	version: '71',
};

function renderSheet(entity: DiscoveryEntity, title?: string, showSource?: boolean) {
	return renderWithProviders(
		<ApiDetailSheet
			entity={entity}
			open
			onClose={() => {}}
			onImport={() => {}}
			importPending={false}
			title={title}
			showSource={showSource}
		/>,
	);
}

describe('ApiDetailSheet subtitle', () => {
	it('drops the domain when it only repeats a bare-domain title, keeping the version', async () => {
		const { container } = renderSheet(ADYEN);

		expect(
			await screen.findByRole('heading', { level: 2, name: 'adyen.com' }),
		).toBeInTheDocument();
		expect(screen.getByTestId('sheet-subtitle')).toHaveTextContent(/^v71$/);
		await checkA11y(container);
	});

	it('omits the subtitle entirely when there is no version either', async () => {
		renderSheet({ ...ADYEN, version: undefined });

		await screen.findByRole('heading', { level: 2, name: 'adyen.com' });
		expect(screen.queryByTestId('sheet-subtitle')).toBeNull();
	});

	it('keeps the domain when the title names the API differently', async () => {
		renderSheet(
			{ ...ADYEN, id: 'github.com', apiId: 'github.com', summary: 'github.com' },
			'GitHub',
		);

		await screen.findByRole('heading', { level: 2, name: 'GitHub' });
		expect(screen.getByTestId('sheet-subtitle')).toHaveTextContent(/^github\.com · v71$/);
	});
});

describe('ApiDetailSheet source', () => {
	it('tags the source only when the loaded entries mix sources', async () => {
		const { unmount } = renderSheet(ADYEN);
		await screen.findByRole('heading', { level: 2, name: 'adyen.com' });
		expect(screen.queryByTestId('sheet-source')).toBeNull();
		unmount();

		renderSheet(
			{ ...ADYEN, sourceSlug: 'internal', entryId: 'internal/adyen.com' },
			undefined,
			true,
		);
		await screen.findByRole('heading', { level: 2, name: 'adyen.com' });
		expect(screen.getByTestId('sheet-source')).toHaveTextContent('internal');
		expect(screen.getByTestId('sheet-entry-id')).toHaveTextContent('internal/adyen.com');
	});

	it('notes collapsed alternates with the explaining tooltip', async () => {
		renderSheet({ ...ADYEN, alternates: 1 });
		const note = await screen.findByTestId('sheet-alternates');
		expect(note).toHaveTextContent('+1 other source');
		expect(note).toHaveAttribute(
			'title',
			'Another catalog source lists this API too; this entry is the one shown.',
		);
	});
});
