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
});
