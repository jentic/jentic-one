import { beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import { renderWithProviders, screen, userEvent, within } from '@/__tests__/test-utils';
import { CredentialType } from '@/shared/api';
import type { SelectedApi } from '@/shared/credentials/api/apis-hooks';
import type { VendorSummary } from '@/shared/credentials/api/vendors-types';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
import { resetCredentialsStore } from '@/shared/credentials/mocks/handlers';

/**
 * A flow already on one API (the setup queue, an API's workspace page,
 * Discover) skips the picker, so the form itself offers the organization's
 * shared OAuth apps for that API, ahead of the manual fields. Only apps whose
 * catalog API matches are offered: a Gmail credential never sees a Calendar
 * app from the same vendor.
 */

const GMAIL: SelectedApi = {
	source: 'catalog',
	vendor: 'googleapis.com',
	name: 'gmail',
	version: '',
	apiId: 'googleapis.com/gmail',
	label: 'Gmail',
};

function sharedApp(id: string, name: string, catalogApiId: string): VendorSummary {
	return {
		entry_id: id,
		registration_id: id,
		key: 'googleapis.com',
		vendor: 'googleapis.com',
		display_name: name,
		name,
		source: 'db',
		flow_kinds: ['authorization_code'],
		catalog_api_id: catalogApiId,
	};
}

function serveVendors(rows: VendorSummary[]): void {
	worker.use(http.get('/vendors', () => HttpResponse.json({ data: rows })));
}

function renderPinned(pinnedApi: SelectedApi = GMAIL): void {
	renderWithProviders(
		<CreateCredentialFlow
			open={true}
			onClose={vi.fn()}
			onCreated={vi.fn()}
			pinnedApi={pinnedApi}
			initialType={CredentialType.OAUTH2}
		/>,
	);
}

describe('CreateCredentialFlow — shared OAuth apps for the chosen API', () => {
	beforeEach(() => {
		resetCredentialsStore();
	});

	it("offers only the pinned API's shared apps ahead of the manual form", async () => {
		serveVendors([
			sharedApp('oar_gmail', 'Acme Gmail', 'googleapis.com/gmail'),
			sharedApp('oar_calendar', 'Acme Calendar', 'googleapis.com/calendar'),
			{ ...sharedApp('gmail', 'Gmail', ''), source: 'config', catalog_api_id: null },
		]);
		renderPinned();

		const section = await screen.findByRole('region', {
			name: "Sign in with your organization's app",
		});
		const tiles = within(section).getAllByTestId('vendor-tile');
		expect(tiles).toHaveLength(1);
		expect(tiles[0]).toHaveTextContent('Acme Gmail');
		expect(within(section).getByText('Shared app')).toBeInTheDocument();
		// The manual form is still there below it.
		expect(screen.getByLabelText(/client id/i)).toBeInTheDocument();
	});

	it('opens the shared app connect and returns to the form on Back', async () => {
		serveVendors([sharedApp('oar_gmail', 'Acme Gmail', 'googleapis.com/gmail')]);
		const user = userEvent.setup();
		renderPinned();

		await user.click(await screen.findByRole('button', { name: /Acme Gmail/ }));
		expect(await screen.findByText('via Acme Gmail')).toBeInTheDocument();

		await user.click(screen.getByRole('button', { name: 'Back' }));
		expect(
			await screen.findByRole('region', { name: "Sign in with your organization's app" }),
		).toBeInTheDocument();
		expect(screen.getByLabelText(/client id/i)).toBeInTheDocument();
	});

	it('shows no shared-app section when none matches the API', async () => {
		serveVendors([sharedApp('oar_calendar', 'Acme Calendar', 'googleapis.com/calendar')]);
		renderPinned();

		expect(await screen.findByLabelText(/client id/i)).toBeInTheDocument();
		expect(
			screen.queryByRole('region', { name: "Sign in with your organization's app" }),
		).not.toBeInTheDocument();
	});
});
