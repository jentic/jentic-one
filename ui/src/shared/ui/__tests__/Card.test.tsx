import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Card, CardHeader, CardBody, CardFooter, CardTitle } from '@/shared/ui/Card';

describe('Card', () => {
	it('renders its sub-sections', () => {
		renderWithProviders(
			<Card>
				<CardHeader>
					<CardTitle>Title</CardTitle>
				</CardHeader>
				<CardBody>Body</CardBody>
				<CardFooter>Footer</CardFooter>
			</Card>,
		);
		expect(screen.getByText('Title')).toBeInTheDocument();
		expect(screen.getByText('Body')).toBeInTheDocument();
		expect(screen.getByText('Footer')).toBeInTheDocument();
	});

	it('renders the title as a heading', () => {
		renderWithProviders(<CardTitle>Heading</CardTitle>);
		expect(screen.getByRole('heading', { name: 'Heading' })).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<Card>
				<CardBody>Accessible card</CardBody>
			</Card>,
		);
		await checkA11y(container);
	});

	it('is a borderless tonal surface; header/footer have no divider by default', () => {
		renderWithProviders(
			<Card data-testid="card">
				<CardHeader>
					<span>Head</span>
				</CardHeader>
				<CardFooter>
					<span>Foot</span>
				</CardFooter>
			</Card>,
		);
		const card = screen.getByTestId('card');
		expect(card).toHaveClass('bg-surface-1', 'rounded-lg');
		expect(getComputedStyle(card).borderTopWidth).toBe('0px');
		expect(getComputedStyle(card).borderTopLeftRadius).toBe('12px');
		expect(getComputedStyle(screen.getByText('Head').parentElement!).borderBottomWidth).toBe(
			'0px',
		);
		expect(getComputedStyle(screen.getByText('Foot').parentElement!).borderTopWidth).toBe(
			'0px',
		);
	});

	it('opts back into an edge, a divider and a selected ring', () => {
		renderWithProviders(
			<Card data-testid="card" outlined selected>
				<CardHeader divider>
					<span>Head</span>
				</CardHeader>
			</Card>,
		);
		const card = screen.getByTestId('card');
		expect(card).toHaveClass('border');
		expect(card).toHaveAttribute('data-selected', 'true');
		expect(getComputedStyle(card).boxShadow).not.toBe('none');
		expect(getComputedStyle(screen.getByText('Head').parentElement!).borderBottomWidth).toBe(
			'1px',
		);
	});

	it('hoverable cards do not move (no translate)', () => {
		renderWithProviders(<Card data-testid="card" hoverable />);
		const card = screen.getByTestId('card');
		expect(card).toHaveClass('card-hover');
		expect(card).not.toHaveClass('card-lift');
		expect(getComputedStyle(card).transform).toBe('none');
	});

	it('CardTitle uses the name tier and the heading font', () => {
		renderWithProviders(<CardTitle>Name</CardTitle>);
		expect(screen.getByRole('heading', { name: 'Name' })).toHaveClass(
			'font-heading',
			'text-foreground-name',
		);
	});
});
