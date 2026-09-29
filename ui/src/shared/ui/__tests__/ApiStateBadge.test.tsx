import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import {
	API_STATE_BADGE_VARIANT,
	API_STATE_LABELS,
	ApiStateBadge,
	ApiStateBadges,
	apiServingState,
} from '@/shared/ui/ApiStateBadge';

describe('ApiStateBadge', () => {
	it('renders the canonical label for each serving state', () => {
		renderWithProviders(
			<>
				<ApiStateBadge state="live" />
				<ApiStateBadge state="draft" />
				<ApiStateBadge state="update" />
			</>,
		);
		expect(screen.getByText('Live')).toBeInTheDocument();
		expect(screen.getByText('Draft')).toBeInTheDocument();
		expect(screen.getByText('Update available')).toBeInTheDocument();
	});

	it('offers a compact update label for dense rows', () => {
		renderWithProviders(<ApiStateBadge state="update" short />);
		expect(screen.getByText('Update')).toBeInTheDocument();
	});

	it('keeps the draft variant aligned with the workspace Draft pill', () => {
		expect(API_STATE_LABELS.draft).toBe('Draft');
		expect(API_STATE_BADGE_VARIANT.draft).toBe('pending');
		expect(API_STATE_BADGE_VARIANT.update).toBe('warning');
	});

	it('derives the state from the registry fields only', () => {
		expect(apiServingState({ currentRevisionId: 'rev_1' }).states).toEqual(['live']);
		expect(apiServingState({ currentRevisionId: null, updateAvailable: true }).states).toEqual([
			'draft',
			'update',
		]);
		expect(apiServingState({ currentRevisionId: 'rev_1', updateAvailable: null }).serving).toBe(
			'live',
		);
	});

	it('renders the primary badge plus Update when flagged', () => {
		renderWithProviders(<ApiStateBadges currentRevisionId={null} updateAvailable short />);
		expect(screen.getByTestId('api-state-draft')).toHaveTextContent('Draft');
		expect(screen.getByTestId('api-state-update')).toHaveTextContent('Update');
	});

	it('has no a11y violations', async () => {
		const { container } = renderWithProviders(<ApiStateBadge state="live" />);
		await checkA11y(container);
	});
});
