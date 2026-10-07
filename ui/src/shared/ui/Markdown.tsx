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
 * keeps working. These are set by the `a` renderer, not passed through from
 * author HTML — `rehype-sanitize` drops any author `target`/`rel`. URL
 * schemes outside the GitHub allowlist (e.g. `javascript:`) are stripped by
 * `rehype-sanitize` and rendered as text.
 *
 * Styled with Tailwind utility classes applied per-element (no global
 * `prose`/Typography plugin) so the look matches the rest of the surface —
 * small, dense, and themed via the shared CSS variables.
 */
import { memo } from 'react';
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
 * allowlist (a/abbr/b/blockquote/br/code/details/em/h1-6/hr/img/li/ol/p/
 * pre/strong/sub/sup/summary/table/td/th/tr/ul …) keeps doing its job.
 *
 * We deliberately do NOT allowlist `target`/`rel` on `<a>`: the `a`
 * component below derives them itself (forcing `noopener noreferrer` on
 * external links). Letting author HTML pass its own `target`/`rel` would
 * allow a description to set `rel="opener"` and re-enable reverse-tabnabbing.
 */
const schema = {
	...defaultSchema,
	attributes: {
		...defaultSchema.attributes,
		th: [...(defaultSchema.attributes?.th ?? []), ['align']],
		td: [...(defaultSchema.attributes?.td ?? []), ['align']],
	},
};

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
		// Strip any author-supplied target/rel; we set them ourselves below so
		// external links always get noopener/noreferrer (the schema also drops
		// these, this is belt-and-braces). Order matters: our values come after
		// the spread so they win even if something slips through.
		const { target: _t, rel: _r, ...safeProps } = props as Record<string, unknown>;
		return (
			<a
				{...safeProps}
				href={href}
				target={isExternal ? '_blank' : undefined}
				rel={isExternal ? 'noreferrer noopener' : undefined}
				className="text-accent-teal underline underline-offset-2"
			>
				{children}
			</a>
		);
	},
	code: ({ className, children, ...props }) => {
		// react-markdown v10 keeps block code (inside `pre`, carries a
		// `language-*` className) distinct from inline code (no className).
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
				className="bg-field text-foreground rounded-sm px-1 font-mono text-[0.85em] [overflow-wrap:anywhere]"
				{...props}
			>
				{children}
			</code>
		);
	},
	pre: ({ children, ...props }) => (
		<pre className="bg-field my-2 overflow-x-auto rounded-md p-2 font-mono text-xs" {...props}>
			{children}
		</pre>
	),
	blockquote: ({ children, ...props }) => (
		<blockquote
			className="border-surface-chip-active text-muted-foreground my-2 border-l-2 pl-3 italic"
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
	hr: (props) => <hr className="border-hairline my-3" {...props} />,
	table: ({ children, ...props }) => (
		<div className="my-2 overflow-x-auto">
			<table className="w-full border-collapse text-xs" {...props}>
				{children}
			</table>
		</div>
	),
	thead: ({ children, ...props }) => (
		<thead className="bg-field" {...props}>
			{children}
		</thead>
	),
	th: ({ children, ...props }) => (
		<th className="px-2 py-1 text-left font-semibold" {...props}>
			{children}
		</th>
	),
	td: ({ children, ...props }) => (
		<td className="border-hairline-row border-b px-2 py-1 align-top" {...props}>
			{children}
		</td>
	),
	img: ({ alt, ...props }) => (
		<img alt={alt ?? ''} className="my-2 max-w-full rounded" {...props} />
	),
};

/**
 * Memoized: the full remark/rehype pipeline (parse → mdast → hast → sanitize)
 * is expensive and there are many instances (per operation, tag, model, and
 * schema property). Memoizing on `source`/`className` keeps a scroll-spy-driven
 * parent re-render from re-running the pipeline for every unchanged instance.
 */
export const Markdown = memo(function Markdown({ source, className }: MarkdownProps) {
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
});
