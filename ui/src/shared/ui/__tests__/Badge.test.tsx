import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Badge, MethodBadge, StatusBadge } from '@/shared/ui/Badge';

describe('Badge', () => {
	it('renders its content', () => {
		renderWithProviders(<Badge>New</Badge>);
		expect(screen.getByText('New')).toBeInTheDocument();
	});

	it('renders a decorative status dot when `dot` is set', () => {
		const { container } = renderWithProviders(
			<Badge variant="pending" dot>
				pending
			</Badge>,
		);
		// The dot is purely decorative — present in the DOM but hidden from AT.
		const dot = container.querySelector('span[aria-hidden="true"]');
		expect(dot).not.toBeNull();
		expect(screen.getByText('pending')).toBeInTheDocument();
	});

	it('omits the dot by default', () => {
		const { container } = renderWithProviders(<Badge>plain</Badge>);
		expect(container.querySelector('span[aria-hidden="true"]')).toBeNull();
	});

	it('MethodBadge upper-cases the method', () => {
		renderWithProviders(<MethodBadge method="get" />);
		expect(screen.getByText('GET')).toBeInTheDocument();
	});

	it('MethodBadge falls back to ? when no method', () => {
		renderWithProviders(<MethodBadge />);
		expect(screen.getByText('?')).toBeInTheDocument();
	});

	it('StatusBadge renders the status code', () => {
		renderWithProviders(<StatusBadge status={503} />);
		expect(screen.getByText('503')).toBeInTheDocument();
	});

	it('StatusBadge renders nothing for a falsy status', () => {
		const { container } = renderWithProviders(<StatusBadge status={0} />);
		expect(container).toBeEmptyDOMElement();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<div>
				<Badge>Tag</Badge>
				<Badge variant="pending" dot>
					pending
				</Badge>
				<MethodBadge method="post" />
				<StatusBadge status={200} />
			</div>,
		);
		await checkA11y(container);
	});
});
