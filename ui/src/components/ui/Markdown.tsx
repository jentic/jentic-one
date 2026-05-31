/**
 * Markdown renderer for OpenAPI `info.description` and similar fields.
 *
 * Powered by `react-markdown` + `remark-gfm` (GitHub-flavoured: tables,
 * strikethrough, task lists, autolinks). Raw HTML is allowed via
 * `rehype-raw` and then run through `rehype-sanitize` with the GitHub
 * preset so descriptions that include `<a>`, `<br>`, `<details>`, etc.
 * render correctly without exposing us to XSS.
 *
 * Anchors get `target="_blank"` + `rel="noreferrer noopener"` only for
 * absolute http(s) URLs; in-app paths stay same-tab so router navigation
 * keeps working. URL schemes outside the GitHub allowlist (e.g.
 * `javascript:`) are stripped by `rehype-sanitize` and rendered as text.
 *
 * The component is intentionally styled with Tailwind utility classes
 * applied per-element (no global `prose`/Typography plugin) so the look
 * matches the rest of the discovery surface — small, dense, and themed
 * via CSS variables.
 */

import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';

interface MarkdownProps {
	source: string;
	className?: string;
}

/**
 * Sanitize schema = GitHub preset + a few attributes we rely on for
 * styling/behaviour. We extend rather than replace so the GitHub
 * allowlist (which already covers a/abbr/b/blockquote/br/code/details
 * /em/h1-6/hr/img/li/ol/p/pre/strong/sub/sup/summary/table/td/th/tr/ul
 * etc.) keeps doing its job.
 */
const schema = {
	...defaultSchema,
	attributes: {
		...defaultSchema.attributes,
		// `target`/`rel` so we can render external links in a new tab
		// without `rehype-sanitize` stripping them back out.
		a: [...(defaultSchema.attributes?.a ?? []), 'target', 'rel'],
		// Tables sometimes carry inline alignment from upstream specs.
		th: [...(defaultSchema.attributes?.th ?? []), ['align']],
		td: [...(defaultSchema.attributes?.td ?? []), ['align']],
	},
};

/**
 * Custom renderers — small, themed, and consistent with the rest of the
 * UI. `prose`-style spacing is handled here per-element instead of via
 * the Tailwind Typography plugin (which we don't pull in) so a single
 * description block doesn't disturb its surrounding card paddings.
 */
const components: Components = {
	h1: ({ children, ...props }) => (
		<h1 className="text-foreground mt-4 mb-2 text-base font-semibold first:mt-0" {...props}>
			{children}
		</h1>
	),
	h2: ({ children, ...props }) => (
		<h2 className="text-foreground mt-4 mb-2 text-sm font-semibold first:mt-0" {...props}>
			{children}
		</h2>
	),
	h3: ({ children, ...props }) => (
		<h3 className="text-foreground mt-3 mb-1.5 text-sm font-semibold first:mt-0" {...props}>
			{children}
		</h3>
	),
	h4: ({ children, ...props }) => (
		<h4 className="text-foreground mt-3 mb-1 text-xs font-semibold first:mt-0" {...props}>
			{children}
		</h4>
	),
	h5: ({ children, ...props }) => (
		<h5 className="text-foreground mt-2 mb-1 text-xs font-semibold first:mt-0" {...props}>
			{children}
		</h5>
	),
	h6: ({ children, ...props }) => (
		<h6
			className="text-muted-foreground mt-2 mb-1 text-xs font-semibold uppercase first:mt-0"
			{...props}
		>
			{children}
		</h6>
	),
	p: ({ children, ...props }) => (
		<p className="my-2 leading-relaxed first:mt-0 last:mb-0" {...props}>
			{children}
		</p>
	),
	ul: ({ children, ...props }) => (
		<ul className="my-2 ml-5 list-disc space-y-0.5 first:mt-0 last:mb-0" {...props}>
			{children}
		</ul>
	),
	ol: ({ children, ...props }) => (
		<ol className="my-2 ml-5 list-decimal space-y-0.5 first:mt-0 last:mb-0" {...props}>
			{children}
		</ol>
	),
	li: ({ children, ...props }) => (
		<li className="leading-relaxed" {...props}>
			{children}
		</li>
	),
	a: ({ href = '', children, ...props }) => {
		const isExternal = /^https?:/i.test(href);
		return (
			<a
				href={href}
				target={isExternal ? '_blank' : undefined}
				rel={isExternal ? 'noreferrer noopener' : undefined}
				className="text-accent-teal underline-offset-2 hover:underline"
				{...props}
			>
				{children}
			</a>
		);
	},
	code: ({ className, children, ...props }) => {
		// `react-markdown` v9 distinguishes block code (gets a
		// `language-*` className from `code` inside `pre`) from inline
		// (`code` with no className). We keep the same node type and
		// just style differently.
		const isBlock = !!className;
		if (isBlock) {
			return (
				<code className={className} {...props}>
					{children}
				</code>
			);
		}
		return (
			<code
				className="bg-muted/60 text-foreground rounded px-1 font-mono text-[0.85em]"
				{...props}
			>
				{children}
			</code>
		);
	},
	pre: ({ children, ...props }) => (
		<pre
			className="bg-muted/40 border-border/40 my-2 overflow-x-auto rounded-md border p-2 font-mono text-xs"
			{...props}
		>
			{children}
		</pre>
	),
	blockquote: ({ children, ...props }) => (
		<blockquote
			className="border-border/60 text-muted-foreground my-2 border-l-2 pl-3 italic"
			{...props}
		>
			{children}
		</blockquote>
	),
	strong: ({ children, ...props }) => (
		<strong className="font-semibold" {...props}>
			{children}
		</strong>
	),
	em: ({ children, ...props }) => <em {...props}>{children}</em>,
	hr: (props) => <hr className="border-border/40 my-3" {...props} />,
	// Tables (GFM). Wrapped so wide tables scroll horizontally inside
	// the narrow sheet rather than blowing out the layout.
	table: ({ children, ...props }) => (
		<div className="my-2 overflow-x-auto">
			<table className="border-border/50 w-full border-collapse border text-xs" {...props}>
				{children}
			</table>
		</div>
	),
	thead: ({ children, ...props }) => (
		<thead className="bg-muted/40" {...props}>
			{children}
		</thead>
	),
	th: ({ children, ...props }) => (
		<th className="border-border/50 border px-2 py-1 text-left font-semibold" {...props}>
			{children}
		</th>
	),
	td: ({ children, ...props }) => (
		<td className="border-border/50 border px-2 py-1 align-top" {...props}>
			{children}
		</td>
	),
	img: ({ alt, ...props }) => (
		<img alt={alt ?? ''} className="my-2 max-w-full rounded" {...props} />
	),
};

export function Markdown({ source, className }: MarkdownProps) {
	return (
		<div className={className}>
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				rehypePlugins={[rehypeRaw, [rehypeSanitize, schema]]}
				components={components}
			>
				{source}
			</ReactMarkdown>
		</div>
	);
}
