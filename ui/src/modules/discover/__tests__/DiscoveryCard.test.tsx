import { describe, it, expect, vi } from 'vitest';
import { render, renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { DiscoveryCard } from '@/modules/discover/components/DiscoveryCard';
import type { DiscoveryEntity, WorkspaceDigestRow } from '@/modules/discover/api';
import type { Credential } from '@/shared/credentials/api';
import { makeDigestRow } from '@/modules/discover/__tests__/digestFixtures';
import { VendorIcon } from '@/shared/ui';
import { vendorIconPropsFor } from '@/shared/lib';

function entity(extra: Partial<DiscoveryEntity> = {}): DiscoveryEntity {
	return {
		id: 'nytimes.com/article_search',
		apiId: 'nytimes.com/article_search',
		summary: 'Article Search',
		subtitle: 'nytimes.com',
		registered: false,
		updateAvailable: false,
		vendor: 'nytimes.com',
		version: '1.0.0',
		githubUrl:
			'https://github.com/jentic/jentic-public-apis/tree/main/apis/openapi/nytimes.com/article_search',
		raw: {},
		...extra,
	};
}

function wsRow(catalogApiId: string, title: string): WorkspaceDigestRow {
	return makeDigestRow(title, {
		ref: { vendor: 'nytimes.com', name: title.toLowerCase(), version: '1.0.0' },
		catalogApiId,
		needsAuth: true,
	});
}

const CRED = { credential_id: 'cred_nyt_1', name: 'NYT key', active: true } as Credential;

function renderCard(props: Partial<React.ComponentProps<typeof DiscoveryCard>> = {}) {
	return renderWithProviders(
		<DiscoveryCard
			entity={entity()}
			active={false}
			onOpen={() => {}}
			onImport={() => {}}
			importPending={false}
			{...props}
		/>,
	);
}

describe('DiscoveryCard', () => {
	it('shows vendor · version and no sub-API chip for an umbrella sub-API', () => {
		renderCard();
		expect(screen.getByTestId('discovery-card-subtitle')).toHaveTextContent(
			/^nytimes\.com·v1\.0\.0$/,
		);
		expect(screen.queryByText(/Sub-API/)).not.toBeInTheDocument();
	});

	it('shows no notes for a bare-domain entry', () => {
		renderCard({
			entity: entity({
				id: 'stripe.com',
				apiId: 'stripe.com',
				summary: 'stripe.com',
				subtitle: 'stripe',
			}),
		});
		expect(screen.queryByTestId('discovery-card-notes')).not.toBeInTheDocument();
	});

	it('available tile: "Credential ready" chip links to the credential inventory, no "add" copy', async () => {
		const { container } = renderCard({ readyCredentials: [CRED] });
		const chip = screen.getByTestId('discovery-card-credential-ready');
		expect(chip).toHaveTextContent(/^Credential ready/);
		expect(chip).not.toHaveTextContent(/add/i);
		expect(chip).toHaveAttribute(
			'title',
			'You already have a credential that covers this API: NYT key',
		);
		const link = screen.getByRole('link', {
			name: /^Credential ready\. You already have a credential that covers this API\./,
		});
		expect(link).toHaveAttribute('href', expect.stringContaining('/agents?credentials=1'));
		await checkA11y(container);
	});

	it('shows no credential note while loading (null) or with no match ([])', () => {
		const { unmount } = renderCard({ readyCredentials: null });
		expect(screen.queryByTestId('discovery-card-credential-ready')).not.toBeInTheDocument();
		unmount();
		renderCard({ readyCredentials: [] });
		expect(screen.queryByTestId('discovery-card-credential-ready')).not.toBeInTheDocument();
	});

	it('imported tile never shows the credential-ready note', () => {
		renderCard({
			entity: entity({ registered: true }),
			readyCredentials: [CRED],
		});
		expect(screen.queryByTestId('discovery-card-credential-ready')).not.toBeInTheDocument();
		expect(screen.getByTestId('card-status-imported')).toBeInTheDocument();
	});

	it('imported tile with one workspace match restores the Live · ops · agents line + update badge', () => {
		const books = { ...wsRow('nytimes.com/books', 'Books'), operationCount: 7 };
		renderCard({
			entity: entity({
				id: 'nytimes.com/books',
				apiId: 'nytimes.com/books',
				summary: 'Books',
				registered: true,
				updateAvailable: true,
			}),
			workspaceMatches: [books],
			matchAgentCount: 2,
		});
		expect(screen.getByTestId('discovery-card-workspace-state')).toHaveTextContent(
			'Live·7 ops·2 agents',
		);
		expect(screen.getByTestId('api-state-update')).toBeInTheDocument();
		expect(screen.getByTestId('card-status-imported')).toBeInTheDocument();
		expect(screen.getByTestId('discovery-card-review-update')).toHaveAttribute(
			'href',
			expect.stringContaining(books.href),
		);
		expect(
			screen.getByRole('link', { name: 'Open Books in your workspace' }),
		).toBeInTheDocument();
	});

	it('renders a truncated agent count as a floor ("50+ agents")', () => {
		const books = wsRow('nytimes.com/books', 'Books');
		renderCard({
			entity: entity({
				id: 'nytimes.com/books',
				apiId: 'nytimes.com/books',
				registered: true,
			}),
			workspaceMatches: [books],
			matchAgentCount: 50,
			matchAgentsAtLeast: true,
		});
		expect(screen.getByTestId('discovery-card-agents')).toHaveTextContent('50+ agents');
	});

	it('primary action reads "Add to workspace", and "Adding…" while pending', () => {
		const { unmount } = renderCard();
		expect(screen.getByTestId('discovery-card-import')).toHaveTextContent('Add to workspace');
		unmount();
		renderCard({ importPending: true });
		expect(screen.getByTestId('discovery-card-import')).toHaveTextContent('Adding…');
		expect(screen.getByTestId('card-status-pending')).toHaveTextContent('Adding…');
	});

	it('keeps behaviour: surface opens the preview, Import imports, GitHub is a secondary link', async () => {
		const user = userEvent.setup();
		const onOpen = vi.fn();
		const onImport = vi.fn();
		renderCard({ onOpen, onImport });
		await user.click(screen.getByRole('button', { name: 'View Article Search' }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		await user.click(screen.getByTestId('discovery-card-import'));
		expect(onImport).toHaveBeenCalledTimes(1);
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(
			screen.getByRole('link', { name: 'View Article Search on GitHub' }),
		).toBeInTheDocument();
	});
	describe('naming and avatar follow the single workspace match', () => {
		const github = entity({
			id: 'github.com',
			apiId: 'github.com',
			summary: 'github.com',
			subtitle: undefined,
			vendor: 'github.com',
			registered: true,
		});
		const ghRow = makeDigestRow('GitHub', {
			ref: { vendor: 'github.com', name: 'github.com', version: '1.1.4' },
			host: 'api.github.com',
			catalogApiId: 'github.com',
		});

		/** The gradient avatar a tile renders (the only gradient element on it). */
		function avatar(root: ParentNode): Element {
			const el = root.querySelector('[class*="bg-gradient-to-br"]');
			if (!el) throw new Error('no VendorIcon avatar');
			return el;
		}

		it('titles the tile with the workspace title and keeps the catalog domain in the subtitle', () => {
			renderCard({ entity: github, workspaceMatches: [ghRow] });
			expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent(/^GitHub$/);
			expect(screen.getByTestId('discovery-card-subtitle')).toHaveTextContent(
				/^github\.com·v1\.0\.0$/,
			);
			expect(screen.getByRole('button', { name: 'View GitHub' })).toBeInTheDocument();
		});

		it('draws the avatar from the same props as the panel / workspace / hub', () => {
			const { container } = renderCard({ entity: github, workspaceMatches: [ghRow] });
			const reference = render(
				<VendorIcon
					{...vendorIconPropsFor({
						title: ghRow.title,
						host: ghRow.host,
						vendor: ghRow.ref.vendor,
						iconUrl: ghRow.iconUrl,
					})}
				/>,
			);
			expect(avatar(container).className).toBe(avatar(reference.container).className);
			expect(avatar(container)).toHaveTextContent('GI');
		});

		it('uses the workspace logo when the match has one', () => {
			const { container } = renderCard({
				entity: github,
				workspaceMatches: [{ ...ghRow, iconUrl: 'https://example.test/gh.png' }],
			});
			expect(container.querySelector('img')).toHaveAttribute(
				'src',
				'https://example.test/gh.png',
			);
		});

		it('keeps the catalog title and mark with no match or several matches', () => {
			const { unmount } = renderCard({ entity: github });
			expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent(/^github\.com$/);
			unmount();
			const { container } = renderCard({
				entity: github,
				workspaceMatches: [ghRow, { ...ghRow, key: 'b', title: 'GitHub v2' }],
			});
			expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent(/^github\.com$/);
			const reference = render(<VendorIcon name="github.com" vendor="github.com" />);
			expect(avatar(container).className).toBe(avatar(reference.container).className);
		});
	});
});
