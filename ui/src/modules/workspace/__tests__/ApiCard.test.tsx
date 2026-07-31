import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen } from '@/__tests__/test-utils';
import { ApiCard } from '@/modules/workspace/components/ApiCard';
import type { WorkspaceApi } from '@/modules/workspace/api';

function makeApi(overrides: Partial<WorkspaceApi> = {}): WorkspaceApi {
	return {
		api: { vendor: 'github.com', name: 'main', version: '1.0.0', host: null },
		catalogApiId: null,
		displayName: null,
		description: null,
		iconUrl: null,
		currentRevisionId: 'rev_1',
		revisionCount: 1,
		operationCount: 3,
		securitySchemes: [],
		createdAt: '2026-05-01T10:00:00Z',
		updatedAt: '2026-05-01T10:00:00Z',
		...overrides,
	};
}

describe('ApiCard', () => {
	it('renders the friendly title as the heading', () => {
		renderWithProviders(<ApiCard api={makeApi()} />);
		expect(screen.getByRole('heading', { name: 'Github.Com' })).toBeInTheDocument();
	});

	it('never renders a blank heading or aria-label when the display name is absent and vendor is empty', () => {
		// `apiRefDisplayName` returns '' here (no display name, empty vendor,
		// generic `main`). The titleFor fallback must keep the heading + the
		// link aria-label non-empty so the card is always identifiable.
		renderWithProviders(
			<ApiCard
				api={makeApi({
					displayName: null,
					api: { vendor: '', name: 'main', version: '1.0.0', host: null },
				})}
			/>,
		);
		const heading = screen.getByRole('heading', { level: 3 });
		expect(heading.textContent?.trim()).not.toBe('');
		// The whole card is a link; its accessible name must not be a bare "Open ".
		const link = screen.getByTestId('workspace-api-card');
		const label = link.getAttribute('aria-label') ?? '';
		expect(label).not.toBe('Open ');
		expect(label.replace(/^Open /, '').trim()).not.toBe('');
	});
});
