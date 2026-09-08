import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { CodeSnippet } from '@/shared/ui/CodeSnippet';
import { Toaster } from '@/shared/ui/Toaster';

describe('CodeSnippet', () => {
	it('renders the code verbatim in a pre block', () => {
		renderWithProviders(<CodeSnippet code="jentic mcp --context my-agent" />);
		const pre = screen.getByText('jentic mcp --context my-agent');
		expect(pre.tagName).toBe('PRE');
	});

	it('renders the optional eyebrow label above the block', () => {
		renderWithProviders(<CodeSnippet label="MCP server command (stdio)" code="jentic mcp" />);
		expect(screen.getByText('MCP server command (stdio)')).toBeInTheDocument();
	});

	it('renders no label element when the label is omitted', () => {
		const { container } = renderWithProviders(<CodeSnippet code="jentic mcp" />);
		expect(container.querySelector('p')).toBeNull();
	});

	it('copies the code via the corner copy button', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<>
				<CodeSnippet code="copy-me" />
				<Toaster />
			</>,
		);
		await user.click(screen.getByRole('button', { name: 'Copy to clipboard' }));
		// The success toast firing proves the real clipboard write resolved
		// (same assertion strategy as CopyButton.test.tsx — the clipboard
		// global can't be reliably spied on in browser mode).
		await waitFor(() => {
			expect(screen.getByText('Copied to clipboard')).toBeInTheDocument();
		});
	});

	it('preserves multi-line code (JSON config blocks)', () => {
		const json = '{\n  "mcpServers": {}\n}';
		renderWithProviders(<CodeSnippet code={json} />);
		expect(screen.getByText(/"mcpServers": \{\}/)).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<CodeSnippet label="Command" code="jentic register --url https://example.test" />,
		);
		await checkA11y(container);
	});
});
