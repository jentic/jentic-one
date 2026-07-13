import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Markdown } from '@/shared/ui/Markdown';

describe('Markdown', () => {
	it('renders GitHub-flavoured markdown (headings, lists, emphasis)', () => {
		renderWithProviders(<Markdown source={'# Title\n\n- one\n- two\n\n**bold** text'} />);
		expect(screen.getByRole('heading', { name: 'Title' })).toBeInTheDocument();
		expect(screen.getByText('one')).toBeInTheDocument();
		expect(screen.getByText('two')).toBeInTheDocument();
		expect(screen.getByText('bold')).toBeInTheDocument();
	});

	it('renders GFM tables', () => {
		renderWithProviders(<Markdown source={'| H1 | H2 |\n| --- | --- |\n| a | b |'} />);
		expect(screen.getByRole('table')).toBeInTheDocument();
		expect(screen.getByRole('columnheader', { name: 'H1' })).toBeInTheDocument();
		expect(screen.getByRole('cell', { name: 'a' })).toBeInTheDocument();
	});

	it('forces noreferrer noopener + target=_blank on external links', () => {
		renderWithProviders(<Markdown source={'[ext](https://example.com)'} />);
		const link = screen.getByRole('link', { name: 'ext' });
		expect(link).toHaveAttribute('href', 'https://example.com');
		expect(link).toHaveAttribute('target', '_blank');
		expect(link).toHaveAttribute('rel', 'noreferrer noopener');
	});

	it('keeps in-app links same-tab (no target/rel)', () => {
		renderWithProviders(<Markdown source={'[home](/dashboard)'} />);
		const link = screen.getByRole('link', { name: 'home' });
		expect(link).toHaveAttribute('href', '/dashboard');
		expect(link).not.toHaveAttribute('target');
		expect(link).not.toHaveAttribute('rel');
	});

	it('strips a script tag injected via raw HTML (XSS)', () => {
		const { container } = renderWithProviders(
			<Markdown source={'hello <script>window.__pwned = true</script> world'} />,
		);
		// Scope to the rendered output (the document also holds harness scripts).
		expect(container.querySelector('script')).toBeNull();
		expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
	});

	it('does not produce a clickable javascript: link', () => {
		renderWithProviders(<Markdown source={'[click](javascript:alert(1))'} />);
		const link = screen.queryByRole('link', { name: 'click' });
		// rehype-sanitize drops the unsafe scheme; if an anchor survives it must
		// not carry the javascript: href.
		expect(link?.getAttribute('href') ?? '').not.toMatch(/javascript:/i);
	});

	it('refuses author-supplied target/rel on raw anchors (no reverse-tabnabbing)', () => {
		renderWithProviders(
			<Markdown
				source={'<a href="https://evil.example" target="_blank" rel="opener">x</a>'}
			/>,
		);
		const link = screen.getByRole('link', { name: 'x' });
		// Our renderer re-derives rel; it can never be the dangerous "opener".
		expect(link).toHaveAttribute('rel', 'noreferrer noopener');
	});

	it('has no a11y violations', async () => {
		const { container } = renderWithProviders(
			<Markdown source={'# Heading\n\nA paragraph with [a link](https://example.com).'} />,
		);
		await checkA11y(container);
	});
});
