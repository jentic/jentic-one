import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import {
	Badge,
	MethodBadge,
	StatusBadge,
	StatusChip,
	StatusText,
	Tag,
	type Variant,
} from '@/shared/ui/Badge';

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

	it('is a borderless sans pill by default; `mono` opts into monospace digits', () => {
		renderWithProviders(
			<div>
				<Badge>Sans</Badge>
				<Badge mono>42</Badge>
			</div>,
		);
		const sans = screen.getByText('Sans');
		expect(sans).toHaveClass('font-sans', 'font-bold');
		expect(sans).not.toHaveClass('border', 'font-mono');
		expect(getComputedStyle(sans).borderTopWidth).toBe('0px');
		expect(screen.getByText('42')).toHaveClass('font-mono');
	});

	it('StatusBadge renders the HTTP code in monospace', () => {
		renderWithProviders(<StatusBadge status={404} />);
		expect(screen.getByText('404')).toHaveClass('font-mono');
		expect(screen.getByText('404')).toHaveAttribute('data-variant', 'warning');
	});

	it('every variant keeps AA text contrast on the page and on a card', async () => {
		const variants: Variant[] = [
			'default',
			'success',
			'warning',
			'danger',
			'pending',
			'neutral',
		];
		const { container } = renderWithProviders(
			<div>
				{variants.map((v) => (
					<Badge key={v} variant={v} dot>
						{v}
					</Badge>
				))}
				<div className="bg-surface-1 p-2">
					{variants.map((v) => (
						<Badge key={v} variant={v}>
							{`${v} on card`}
						</Badge>
					))}
					<Tag>Category</Tag>
				</div>
			</div>,
		);
		await checkA11y(container);
	});

	it('MethodBadge is a borderless fixed-width chip; only the word is tinted', async () => {
		const { container } = renderWithProviders(
			<div className="bg-surface-1 p-2">
				{['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'].map((m) => (
					<MethodBadge key={m} method={m} />
				))}
			</div>,
		);
		const get = screen.getByText('GET');
		expect(get).toHaveClass('bg-surface-chip', 'w-[58px]', 'text-foreground-sub');
		expect(getComputedStyle(get).borderTopWidth).toBe('0px');
		expect(screen.getByText('POST')).toHaveClass('text-method-post');
		expect(screen.getByText('DELETE')).toHaveClass('text-method-delete');
		expect(screen.getByText('PATCH')).toHaveClass('text-method-put');
		// Unknown verbs fall back to the neutral word.
		expect(screen.getByText('OPTIONS')).toHaveClass('text-foreground-sub');
		await checkA11y(container);
	});
});

describe('warm states (caution / warning)', () => {
	const Glyph = (props: { className?: string }) => <svg data-testid="glyph" {...props} />;

	it('a warning Badge is a neutral tonal chip — the word is never tinted', () => {
		renderWithProviders(<Badge variant="warning">Disabled</Badge>);
		const pill = screen.getByText('Disabled');
		expect(pill).toHaveClass('bg-surface-tonal', 'text-foreground-lighter');
		expect(pill.className).not.toMatch(/text-warning/);
	});

	it('StatusText caution keeps the word grey and tints only its glyph', async () => {
		const { container } = renderWithProviders(
			<StatusText tone="caution" icon={Glyph}>
				No rules — all calls blocked
			</StatusText>,
		);
		const text = screen.getByText('No rules — all calls blocked');
		expect(text).toHaveClass('text-foreground-sub');
		expect(text).toHaveAttribute('data-tone', 'caution');
		expect(screen.getByTestId('glyph')).toHaveClass('text-caution');
		await checkA11y(container);
	});

	it('StatusChip is a tonal chip with a tinted glyph', async () => {
		const { container } = renderWithProviders(<StatusChip icon={Glyph}>Suspended</StatusChip>);
		const chip = screen.getByText('Suspended');
		expect(chip).toHaveClass('bg-surface-tonal', 'text-foreground-lighter');
		expect(screen.getByTestId('glyph')).toHaveClass('text-caution');
		await checkA11y(container);
	});
});
