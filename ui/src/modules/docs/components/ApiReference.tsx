/**
 * ApiReferenceView — a native, theme-matched OpenAPI reference.
 *
 * The spec drives everything (see lib/apiSpec.parseSpec): we render every tag
 * group, tag, operation, request/response body schema, and the component
 * schemas ("Models") in standard OpenAPI ordering (x-tagGroups → tags →
 * operations). Each operation is enriched with our scope reference (the one
 * thing the spec lacks) joined on `(method,path)`.
 *
 * Layout mirrors the CLI reference: a sticky grouped index on the left + one
 * scrolling document on the right + scroll-spy, so the two reference sections
 * feel like one product.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Filter, ChevronRight, Lock, Globe } from 'lucide-react';
import type {
	OpenApiDocument,
	ReferenceEndpoint,
	ReferencePayload,
} from '@/modules/docs/api/types';
import {
	parseSpec,
	type ParsedSpec,
	type SpecOperation,
	type SpecBody,
} from '@/modules/docs/lib/apiSpec';
import {
	lookupKey,
	indexReference,
	operationAnchorId,
	scrollToAnchor,
} from '@/modules/docs/lib/anchor';
import { ScopePanel } from '@/modules/docs/components/ScopePanel';
import { SchemaView, modelAnchorId } from '@/modules/docs/components/SchemaView';
import { useScrollSpy } from '@/modules/docs/lib/useScrollSpy';
import { MethodBadge, Markdown, Input, LazyMount } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

/* ---- anchors ------------------------------------------------------------- */

/**
 * Anchors are namespaced by an optional `prefix` so two ApiReferenceView
 * instances on the same page (the control plane + the standalone Broker) never
 * collide on the shared `MODELS_ANCHOR` constant or on like-named tag groups.
 * The control-plane instance uses the empty default (anchors unchanged), the
 * Broker passes its own prefix.
 */
export function tagGroupAnchorId(name: string, prefix = ''): string {
	return `${prefix}apigroup-${name}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}
function tagAnchorId(name: string, prefix = ''): string {
	return `${prefix}apitag-${name}`.replace(/[^a-zA-Z0-9_-]+/g, '-');
}
function opAnchorId(op: SpecOperation, prefix = ''): string {
	const base = operationAnchorId(op.method, op.path);
	return prefix ? `${prefix}-${base}` : base;
}
export const MODELS_ANCHOR = 'api-models';
function modelsAnchorId(prefix = ''): string {
	return prefix ? `${prefix}-${MODELS_ANCHOR}` : MODELS_ANCHOR;
}
function prefixedModelAnchorId(name: string, prefix = ''): string {
	const base = modelAnchorId(name);
	return prefix ? `${prefix}-${base}` : base;
}

/* ---- filtering ----------------------------------------------------------- */

function opMatches(op: SpecOperation, q: string): boolean {
	if (!q) return true;
	return `${op.method} ${op.path} ${op.summary ?? ''} ${op.operationId ?? ''} ${op.tags.join(' ')}`
		.toLowerCase()
		.includes(q);
}

/** Apply the filter to the parsed model, dropping empty tags/groups. */
function filterSpec(parsed: ParsedSpec, q: string): ParsedSpec {
	if (!q) return parsed;
	const groups = parsed.groups
		.map((g) => ({
			...g,
			tags: g.tags
				.map((t) => ({ ...t, operations: t.operations.filter((o) => opMatches(o, q)) }))
				.filter((t) => t.operations.length > 0),
		}))
		.filter((g) => g.tags.length > 0);
	const models = parsed.models.filter((m) => m.name.toLowerCase().includes(q));
	return { ...parsed, groups, models };
}

/* ---- index --------------------------------------------------------------- */

function jumpTo(id: string) {
	scrollToAnchor(id);
}

/**
 * A single operation row in the left index. Memoized so a scroll-spy `activeId`
 * change re-renders only the two rows whose `active` flips, not all ~hundreds
 * of rows — the index is the dominant render cost on large specs.
 */
const IndexOpRow = memo(function IndexOpRow({
	id,
	method,
	path,
	active,
}: {
	id: string;
	method: string;
	path: string;
	active: boolean;
}) {
	return (
		<li>
			<button
				type="button"
				data-index-for={id}
				aria-current={active ? 'location' : undefined}
				onClick={() => jumpTo(id)}
				className={cn(
					'flex w-full items-center gap-1.5 rounded px-2 py-1 text-left transition-colors',
					active ? 'bg-primary/10' : 'hover:bg-muted',
				)}
				title={`${method} ${path}`}
			>
				<MethodBadge method={method} />
				<code
					className={cn(
						'truncate font-mono text-[12px]',
						active ? 'text-primary' : 'text-foreground/65',
					)}
				>
					{path}
				</code>
			</button>
		</li>
	);
});

/** A single model row in the left index. Memoized for the same reason as IndexOpRow. */
const IndexModelRow = memo(function IndexModelRow({
	id,
	name,
	active,
}: {
	id: string;
	name: string;
	active: boolean;
}) {
	return (
		<li>
			<button
				type="button"
				data-index-for={id}
				aria-current={active ? 'location' : undefined}
				onClick={() => jumpTo(id)}
				className={cn(
					'hover:bg-muted hover:text-foreground block w-full truncate rounded px-2 py-1 text-left font-mono text-[12px]',
					active ? 'bg-primary/10 text-primary' : 'text-foreground/60',
				)}
			>
				{name}
			</button>
		</li>
	);
});

function ReferenceIndex({
	parsed,
	activeId,
	showModels,
	prefix = '',
}: {
	parsed: ParsedSpec;
	activeId: string | null;
	showModels: boolean;
	prefix?: string;
}) {
	const navRef = useRef<HTMLElement | null>(null);

	// Keep the highlighted operation in view as the reader scrolls the document
	// (and after a click) — the long index otherwise leaves the active entry
	// off-screen. Mirrors the CLI command index.
	useEffect(() => {
		if (!activeId) return;
		const nav = navRef.current;
		if (!nav) return;
		const el = nav.querySelector<HTMLElement>(`[data-index-for="${CSS.escape(activeId)}"]`);
		if (!el) return;
		const navBox = nav.getBoundingClientRect();
		const elBox = el.getBoundingClientRect();
		if (elBox.top < navBox.top || elBox.bottom > navBox.bottom) {
			el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
		}
	}, [activeId]);

	return (
		<nav
			ref={navRef}
			aria-label="API operations"
			className="hidden lg:sticky lg:top-24 lg:block lg:max-h-[calc(100vh-7rem)] lg:self-start lg:overflow-y-auto lg:pr-1"
		>
			<div className="space-y-5">
				{parsed.groups.map((group) => (
					<div key={group.name}>
						<button
							type="button"
							onClick={() => jumpTo(tagGroupAnchorId(group.name, prefix))}
							className="text-foreground/45 hover:text-foreground mb-1 px-2 text-left text-[10px] font-semibold tracking-wider uppercase"
						>
							{group.name}
						</button>
						{group.tags.map((tag) => (
							<div key={tag.name} className="mb-2">
								<button
									type="button"
									onClick={() => jumpTo(tagAnchorId(tag.name, prefix))}
									className="text-foreground/70 hover:text-foreground mb-0.5 px-2 text-left text-[11px] font-medium"
								>
									{tag.name}
								</button>
								<ul className="space-y-0.5">
									{tag.operations.map((op) => {
										const id = opAnchorId(op, prefix);
										return (
											<IndexOpRow
												key={id}
												id={id}
												method={op.method}
												path={op.path}
												active={id === activeId}
											/>
										);
									})}
								</ul>
							</div>
						))}
					</div>
				))}

				{showModels && (
					<div>
						<button
							type="button"
							onClick={() => jumpTo(modelsAnchorId(prefix))}
							className="text-foreground/45 hover:text-foreground mb-1 px-2 text-left text-[10px] font-semibold tracking-wider uppercase"
						>
							Models
						</button>
						<ul className="space-y-0.5">
							{parsed.models.map((m) => {
								const id = prefixedModelAnchorId(m.name, prefix);
								return (
									<IndexModelRow
										key={m.name}
										id={id}
										name={m.name}
										active={id === activeId}
									/>
								);
							})}
						</ul>
					</div>
				)}
			</div>
		</nav>
	);
}

/* ---- bodies -------------------------------------------------------------- */

/** A single media-type body's schema (used inside disclosures). */
function BodySchema({
	spec,
	body,
	anchorPrefix = '',
}: {
	spec: OpenApiDocument;
	body: SpecBody;
	anchorPrefix?: string;
}) {
	return (
		<div>
			<p className="text-foreground/45 mb-1.5 font-mono text-[11px]">{body.contentType}</p>
			{body.schema && Object.keys(body.schema).length > 0 ? (
				<SchemaView spec={spec} schema={body.schema} anchorPrefix={anchorPrefix} />
			) : (
				<p className="text-foreground/45 text-[12px] italic">No schema.</p>
			)}
		</div>
	);
}

/* ---- disclosure ---------------------------------------------------------- */

/**
 * A collapsible row whose body is only mounted while open — so the heavy schema
 * trees for request/response bodies aren't rendered until the reader asks for
 * them (keeps each operation light and scannable by default).
 */
function Disclosure({
	summary,
	defaultOpen = false,
	children,
}: {
	summary: ReactNode;
	defaultOpen?: boolean;
	children: ReactNode;
}) {
	const [open, setOpen] = useState(defaultOpen);
	return (
		<div className="border-border/40 bg-card/30 overflow-hidden rounded-lg border">
			<button
				type="button"
				onClick={() => setOpen((v) => !v)}
				aria-expanded={open}
				className="hover:bg-muted/30 flex w-full items-start gap-2 px-3 py-2 text-left transition-colors"
			>
				<ChevronRight
					className={cn(
						'text-foreground/40 mt-0.5 h-3.5 w-3.5 shrink-0 transition-transform',
						open && 'rotate-90',
					)}
					aria-hidden="true"
				/>
				<span className="min-w-0 flex-1">{summary}</span>
			</button>
			{open && <div className="border-border/40 border-t px-3 py-3">{children}</div>}
		</div>
	);
}

/** Status pill, coloured by class (2xx success, 4xx/5xx danger, else neutral). */
function StatusPill({ status }: { status: string }) {
	return (
		<code
			className={cn(
				'shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold',
				status.startsWith('2')
					? 'bg-success/15 text-success'
					: status.startsWith('4') || status.startsWith('5')
						? 'bg-danger/15 text-danger'
						: 'bg-muted/40 text-foreground/70',
			)}
		>
			{status}
		</code>
	);
}

function SectionLabel({ children, count }: { children: ReactNode; count?: number }) {
	return (
		<h4 className="text-foreground/60 mb-2 flex items-baseline gap-2 text-[11px] font-semibold tracking-wider uppercase">
			{children}
			{count != null && (
				<span className="text-foreground/45 font-mono text-[10px] normal-case">
					{count}
				</span>
			)}
		</h4>
	);
}

/* ---- parameters ---------------------------------------------------------- */

const PARAM_GROUP_LABEL: Record<string, string> = {
	path: 'Path',
	query: 'Query',
	header: 'Header',
	cookie: 'Cookie',
};

/** Group params by location, in a stable, conventional order. */
function groupParams(params: SpecOperation['parameters']) {
	const order = ['path', 'query', 'header', 'cookie'];
	const byIn = new Map<string, SpecOperation['parameters']>();
	for (const p of params) {
		const key = p.in || 'other';
		const list = byIn.get(key);
		if (list) list.push(p);
		else byIn.set(key, [p]);
	}
	return [...byIn.entries()].sort(
		(a, b) => (order.indexOf(a[0]) + 1 || 99) - (order.indexOf(b[0]) + 1 || 99),
	);
}

function ParamRow({ p }: { p: SpecOperation['parameters'][number] }) {
	return (
		<div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 py-2 sm:grid-cols-[14rem_minmax(0,1fr)]">
			<div className="min-w-0">
				<div className="flex flex-wrap items-baseline gap-1.5">
					<code className="text-foreground font-mono text-[13px]">{p.name}</code>
					{p.required && (
						<span className="text-danger text-[10px] font-medium">required</span>
					)}
				</div>
				{p.type && <code className="text-primary/70 font-mono text-[11px]">{p.type}</code>}
			</div>
			<div className="min-w-0">
				{p.description && (
					<p className="text-foreground/65 text-[13px] leading-snug">{p.description}</p>
				)}
				{p.constraints && p.constraints.length > 0 && (
					<div className="mt-1 flex flex-wrap gap-1">
						{p.constraints.map((c) => (
							<span
								key={c}
								className="bg-muted/40 text-foreground/55 rounded px-1 py-px font-mono text-[10px]"
							>
								{c}
							</span>
						))}
					</div>
				)}
				{!p.description && (!p.constraints || p.constraints.length === 0) && (
					<span className="text-foreground/30 text-[13px]">—</span>
				)}
			</div>
		</div>
	);
}

/* ---- operation ----------------------------------------------------------- */

/** A compact auth chip for the operation header (public vs. scoped). */
function AuthChip({ endpoint }: { endpoint: ReferenceEndpoint | undefined }) {
	if (!endpoint) return null;
	if (!endpoint.authenticated) {
		return (
			<span className="text-success/90 inline-flex items-center gap-1 text-[11px] font-medium">
				<Globe className="h-3 w-3" aria-hidden="true" />
				Public
			</span>
		);
	}
	const scopes = endpoint.required_scopes ?? [];
	return (
		<span className="text-foreground/55 inline-flex items-center gap-1 text-[11px]">
			<Lock className="h-3 w-3" aria-hidden="true" />
			{scopes.length > 0 ? (
				<code className="font-mono">
					{scopes[0]}
					{scopes.length > 1 ? ` +${scopes.length - 1}` : ''}
				</code>
			) : (
				'Authenticated'
			)}
		</span>
	);
}

const OperationBlock = memo(function OperationBlock({
	op,
	spec,
	endpoint,
	anchorPrefix = '',
}: {
	op: SpecOperation;
	spec: OpenApiDocument;
	endpoint: ReferenceEndpoint | undefined;
	anchorPrefix?: string;
}) {
	const paramGroups = useMemo(() => groupParams(op.parameters), [op.parameters]);
	const reqBodies = op.requestBodies;

	return (
		<article className="border-border/60 border-b py-8 first:pt-1 last:border-b-0">
			{/* Header: method + path + auth chip — identity at a glance. */}
			<div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
				<MethodBadge method={op.method} />
				<code className="text-foreground font-mono text-[15px] font-semibold break-all">
					{op.path}
				</code>
				{op.deprecated && (
					<span className="text-danger border-danger/40 rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase">
						deprecated
					</span>
				)}
				<span className="ml-auto">
					<AuthChip endpoint={endpoint} />
				</span>
			</div>

			{op.summary && (
				<p className="text-foreground mt-2.5 text-[15px] font-medium">{op.summary}</p>
			)}
			{op.operationId && (
				<p className="text-foreground/60 mt-0.5 font-mono text-[11px]">
					operationId: {op.operationId}
				</p>
			)}
			{op.description && op.description !== op.summary && (
				<Markdown
					source={op.description}
					className="text-foreground/65 mt-1.5 text-sm leading-relaxed"
				/>
			)}

			{/* Authorization — the unique value of this reference, kept prominent. */}
			{endpoint && (
				<div className="mt-4">
					<ScopePanel endpoint={endpoint} />
				</div>
			)}

			{/* Request: parameters (open — essential) + body (collapsed — heavy). */}
			{(paramGroups.length > 0 || reqBodies.length > 0) && (
				<div className="mt-5">
					<SectionLabel count={op.parameters.length || undefined}>Request</SectionLabel>
					<div className="space-y-3">
						{paramGroups.map(([loc, params]) => (
							<div key={loc} className="border-border/40 rounded-lg border px-3 py-1">
								<p className="text-foreground/40 border-border/30 border-b py-1.5 text-[10px] font-semibold tracking-wider uppercase">
									{PARAM_GROUP_LABEL[loc] ?? loc} parameters
								</p>
								<div className="divide-border/25 divide-y">
									{params.map((p) => (
										<ParamRow key={`${p.in}-${p.name}`} p={p} />
									))}
								</div>
							</div>
						))}

						{reqBodies.map((reqBody, i) => (
							<Disclosure
								key={reqBody.contentType}
								summary={
									<span className="flex flex-wrap items-baseline gap-2">
										<span className="text-foreground text-[13px] font-medium">
											Request body
										</span>
										{op.requestRequired && i === 0 && (
											<span className="text-danger text-[10px] font-medium">
												required
											</span>
										)}
										<code className="text-foreground/45 font-mono text-[11px]">
											{reqBody.contentType}
										</code>
									</span>
								}
							>
								<BodySchema
									spec={spec}
									body={reqBody}
									anchorPrefix={anchorPrefix}
								/>
							</Disclosure>
						))}
					</div>
				</div>
			)}

			{/* Responses: a list of collapsed status rows — nothing heavy renders
			    until the reader opens one. */}
			{op.responses.length > 0 && (
				<div className="mt-5">
					<SectionLabel count={op.responses.length}>Responses</SectionLabel>
					<div className="space-y-2">
						{op.responses.map((r) => {
							const hasBody = r.bodies.length > 0;
							const summary = (
								<span className="flex items-baseline gap-2">
									<StatusPill status={r.status} />
									<span className="text-foreground/70 min-w-0 flex-1 text-[13px]">
										{r.description || '—'}
									</span>
									{hasBody && (
										<code className="text-foreground/35 shrink-0 font-mono text-[10px]">
											{r.bodies[0].contentType}
										</code>
									)}
								</span>
							);
							// Responses with no schema need no disclosure — just show the row.
							if (!hasBody) {
								return (
									<div
										key={r.status}
										className="border-border/40 bg-card/30 flex items-baseline gap-2 rounded-lg border px-3 py-2"
									>
										{summary}
									</div>
								);
							}
							return (
								<Disclosure key={r.status} summary={summary}>
									{r.bodies.map((b) => (
										<BodySchema
											key={b.contentType}
											spec={spec}
											body={b}
											anchorPrefix={anchorPrefix}
										/>
									))}
								</Disclosure>
							);
						})}
					</div>
				</div>
			)}
		</article>
	);
});

/* ---- groups / tags ------------------------------------------------------- */

function TagSection({
	tag,
	spec,
	refIndex,
	prefix = '',
}: {
	tag: ParsedSpec['groups'][number]['tags'][number];
	spec: OpenApiDocument;
	refIndex: Map<string, ReferenceEndpoint>;
	prefix?: string;
}) {
	return (
		<section id={tagAnchorId(tag.name, prefix)} className="scroll-mt-28">
			<header className="mb-2">
				<h4 className="text-foreground/90 text-sm font-semibold tracking-wide">
					{tag.name}
				</h4>
				{tag.description && (
					<Markdown
						source={tag.description}
						className="text-foreground/55 mt-0.5 text-sm"
					/>
				)}
			</header>
			<div>
				{tag.operations.map((op) => (
					<LazyMount
						key={opAnchorId(op, prefix)}
						id={opAnchorId(op, prefix)}
						className="scroll-mt-28"
						minHeight={160}
					>
						<OperationBlock
							op={op}
							spec={spec}
							endpoint={refIndex.get(lookupKey(op.method, op.path))}
							anchorPrefix={prefix}
						/>
					</LazyMount>
				))}
			</div>
		</section>
	);
}

/* ---- models -------------------------------------------------------------- */

function ModelsSection({
	spec,
	parsed,
	prefix = '',
}: {
	spec: OpenApiDocument;
	parsed: ParsedSpec;
	prefix?: string;
}) {
	if (parsed.models.length === 0) return null;
	return (
		<section id={modelsAnchorId(prefix)} className="scroll-mt-28">
			<header className="border-border bg-card/40 mb-4 rounded-lg border px-4 py-2.5">
				<h3 className="text-foreground text-sm font-semibold">Models</h3>
				<p className="text-foreground/60 text-xs">
					{parsed.models.length} component schemas referenced by the operations above.
				</p>
			</header>
			<div className="space-y-6">
				{parsed.models.map((m) => (
					<LazyMount
						key={m.name}
						id={prefixedModelAnchorId(m.name, prefix)}
						className="border-border/40 scroll-mt-28 border-b pb-6"
						minHeight={120}
					>
						<code className="text-foreground font-mono text-sm font-semibold">
							{m.name}
						</code>
						{(() => {
							const desc =
								m.schema &&
								typeof m.schema === 'object' &&
								'description' in m.schema
									? (m.schema as { description?: string }).description
									: undefined;
							return desc ? (
								<Markdown
									source={desc}
									className="text-foreground/55 mt-1 text-[13px]"
								/>
							) : null;
						})()}
						<div className="mt-2">
							<SchemaView spec={spec} schema={m.schema} anchorPrefix={prefix} />
						</div>
					</LazyMount>
				))}
			</div>
		</section>
	);
}

/* ---- overview (servers + auth schemes) ----------------------------------- */

/** Human one-liner for a security scheme object. */
function describeScheme(s: Record<string, unknown>): string {
	const type = typeof s.type === 'string' ? s.type : 'unknown';
	if (type === 'http') {
		const scheme = typeof s.scheme === 'string' ? s.scheme : '';
		const fmt = typeof s.bearerFormat === 'string' ? ` (${s.bearerFormat})` : '';
		return `HTTP ${scheme}${fmt}`.trim();
	}
	if (type === 'apiKey') {
		const where = typeof s.in === 'string' ? s.in : '';
		const name = typeof s.name === 'string' ? `: ${s.name}` : '';
		return `API key in ${where}${name}`;
	}
	if (type === 'oauth2') return 'OAuth 2.0';
	if (type === 'openIdConnect') return 'OpenID Connect';
	return type;
}

/** Pull the OAuth flows from a scheme into a flat, render-ready list. */
function oauthFlows(
	s: Record<string, unknown>,
): { name: string; tokenUrl?: string; authUrl?: string; scopes: [string, string][] }[] {
	const flows = s.flows;
	if (typeof flows !== 'object' || flows === null) return [];
	return Object.entries(flows as Record<string, unknown>).map(([name, f]) => {
		const flow = (typeof f === 'object' && f !== null ? f : {}) as Record<string, unknown>;
		const scopesObj =
			typeof flow.scopes === 'object' && flow.scopes !== null
				? (flow.scopes as Record<string, unknown>)
				: {};
		return {
			name,
			tokenUrl: typeof flow.tokenUrl === 'string' ? flow.tokenUrl : undefined,
			authUrl: typeof flow.authorizationUrl === 'string' ? flow.authorizationUrl : undefined,
			scopes: Object.entries(scopesObj).map(
				([k, v]) => [k, typeof v === 'string' ? v : ''] as [string, string],
			),
		};
	});
}

/**
 * One security scheme as an expandable card. Collapsed it states the type in a
 * line; opened it shows the description (how to send the token), the OAuth
 * flow's token URL, and the scope catalogue the scheme documents — so a reader
 * can see "how it looks" and how to use it without leaving the page.
 */
function SchemeCard({ name, scheme }: { name: string; scheme: Record<string, unknown> }) {
	const desc = typeof scheme.description === 'string' ? scheme.description : undefined;
	const flows = oauthFlows(scheme);
	const hasDetail = !!desc || flows.length > 0;

	const summary = (
		<span className="flex flex-wrap items-baseline gap-2">
			<code className="text-primary font-mono text-[13px]">{name}</code>
			<span className="text-foreground/55 text-xs">{describeScheme(scheme)}</span>
		</span>
	);

	if (!hasDetail) {
		return (
			<div className="border-border/40 bg-card/30 rounded-lg border px-3 py-2">{summary}</div>
		);
	}

	return (
		<Disclosure summary={summary}>
			{desc && <Markdown source={desc} className="text-foreground/65 text-[13px]" />}
			{flows.map((flow) => (
				<div key={flow.name} className="mt-3 first:mt-2">
					<p className="text-foreground/45 text-[11px] font-semibold tracking-wider uppercase">
						{flow.name} flow
					</p>
					{flow.tokenUrl && (
						<p className="text-foreground/60 mt-1 text-[12px]">
							Token URL:{' '}
							<code className="bg-muted/40 text-foreground rounded px-1 py-0.5 font-mono text-[11px]">
								{flow.tokenUrl}
							</code>
						</p>
					)}
					{flow.scopes.length > 0 && (
						<ul className="border-border/40 mt-2 space-y-1 border-l pl-3">
							{flow.scopes.map(([scope, scopeDesc]) => (
								<li key={scope} className="flex flex-wrap items-baseline gap-2">
									<code className="bg-primary/15 text-primary rounded px-1.5 py-0.5 font-mono text-[11px]">
										{scope}
									</code>
									{scopeDesc && (
										<span className="text-foreground/55 text-[12px]">
											{scopeDesc}
										</span>
									)}
								</li>
							))}
						</ul>
					)}
				</div>
			))}
		</Disclosure>
	);
}

/**
 * The reference overview: base server URL(s) and the security schemes the spec
 * declares. These are top-level OpenAPI fields (`servers`, `info`,
 * `components.securitySchemes`) that the per-operation view can't show — without
 * them the reader doesn't know where to send requests or how to authenticate.
 */
function ApiOverview({ parsed }: { parsed: ParsedSpec }) {
	const schemes = Object.entries(parsed.securitySchemes);
	// Only invite the reader to "expand a scheme" when at least one actually has
	// expandable detail (a description or OAuth flows/scopes) — otherwise the
	// hint points at non-interactive cards (e.g. a bare bearer scheme).
	const schemesHaveDetail = schemes.some(
		([, s]) => typeof s.description === 'string' || oauthFlows(s).length > 0,
	);
	const { meta } = parsed;
	const hasMeta = !!(
		meta.contactName ||
		meta.contactEmail ||
		meta.contactUrl ||
		meta.licenseName
	);
	if (parsed.servers.length === 0 && schemes.length === 0 && !hasMeta) return null;

	return (
		<section className="border-border bg-card/40 space-y-4 rounded-lg border p-4">
			<div className="grid gap-4 sm:grid-cols-2">
				{parsed.servers.length > 0 && (
					<div>
						<SectionLabel>Base URL</SectionLabel>
						<ul className="space-y-1">
							{parsed.servers.map((s) => (
								<li key={s.url}>
									<code className="text-foreground bg-muted/40 rounded px-1.5 py-0.5 font-mono text-[13px]">
										{s.url}
									</code>
									{s.description && (
										<span className="text-foreground/60 ml-2 text-xs">
											{s.description}
										</span>
									)}
								</li>
							))}
						</ul>
					</div>
				)}

				{hasMeta && (
					<div>
						<SectionLabel>About</SectionLabel>
						<div className="text-foreground/60 flex flex-wrap gap-x-4 gap-y-1 text-xs">
							{meta.contactName && (
								<span>
									Contact:{' '}
									{meta.contactEmail ? (
										<a
											href={`mailto:${meta.contactEmail}`}
											className="text-primary underline"
										>
											{meta.contactName}
										</a>
									) : (
										meta.contactName
									)}
								</span>
							)}
							{meta.contactUrl && (
								<a
									href={meta.contactUrl}
									target="_blank"
									rel="noreferrer"
									className="text-primary underline"
								>
									Website
								</a>
							)}
							{meta.licenseName && (
								<span>
									License:{' '}
									{meta.licenseUrl ? (
										<a
											href={meta.licenseUrl}
											target="_blank"
											rel="noreferrer"
											className="text-primary underline"
										>
											{meta.licenseName}
										</a>
									) : (
										meta.licenseName
									)}
								</span>
							)}
						</div>
					</div>
				)}
			</div>

			{schemes.length > 0 && (
				<div>
					<SectionLabel>Authentication</SectionLabel>
					<p className="text-foreground/50 mb-2 text-xs">
						Every authenticated request sends{' '}
						<code className="bg-muted/40 text-foreground/70 rounded px-1 py-0.5 font-mono text-[11px]">
							Authorization: Bearer &lt;token&gt;
						</code>
						.{schemesHaveDetail ? ' Expand a scheme for details.' : ''}
					</p>
					<div className="space-y-1.5">
						{schemes.map(([name, s]) => (
							<SchemeCard key={name} name={name} scheme={s} />
						))}
					</div>
				</div>
			)}
		</section>
	);
}

/* ---- top-level ----------------------------------------------------------- */

export interface ApiReferenceViewProps {
	payload: ReferencePayload;
	spec: OpenApiDocument;
	/**
	 * Pre-parsed spec, if the caller already parsed it (DocsPage parses once to
	 * build the rail's sub-anchors and passes it down so we don't parse twice —
	 * the full spec is large). Falls back to parsing `spec` here when absent.
	 */
	parsedSpec?: ParsedSpec;
	/**
	 * Anchor namespace, so a page can host two references (the control plane +
	 * the standalone Broker) without colliding on the shared `MODELS_ANCHOR`
	 * constant or like-named tag groups. Empty (default) leaves anchors as-is.
	 */
	anchorPrefix?: string;
}

export function ApiReferenceView({
	payload,
	spec,
	parsedSpec,
	anchorPrefix = '',
}: ApiReferenceViewProps) {
	const [filter, setFilter] = useState('');
	const q = filter.trim().toLowerCase();

	const parsedLocal = useMemo(() => parseSpec(spec), [spec]);
	const parsedFull = parsedSpec ?? parsedLocal;
	const parsed = useMemo(() => filterSpec(parsedFull, q), [parsedFull, q]);
	const refIndex = useMemo(() => indexReference(payload), [payload]);

	const matchTotal = parsed.groups.reduce(
		(n, g) => n + g.tags.reduce((m, t) => m + t.operations.length, 0),
		0,
	);

	const spyIds = useMemo(
		() => [
			...parsed.groups.flatMap((g) =>
				g.tags.flatMap((t) => t.operations.map((op) => opAnchorId(op, anchorPrefix))),
			),
			...parsed.models.map((m) => prefixedModelAnchorId(m.name, anchorPrefix)),
		],
		[parsed, anchorPrefix],
	);
	const activeId = useScrollSpy(spyIds, '-120px 0px -70% 0px', false);

	const empty = parsed.groups.length === 0 && parsed.models.length === 0;

	return (
		<div className="space-y-4">
			{/* API metadata (base URL, auth schemes, contact) sits above the filter —
			    it's page-level context the reader wants before scanning operations. */}
			<ApiOverview parsed={parsed} />

			{/* Sticky filter — stays reachable while reading the long reference.
			    (Filter icon per design system; global search lives in the navbar.)
			    Only sticky on lg+; on mobile it scrolls away so it never competes
			    with the sticky section navbar for the limited vertical space. */}
			<div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 z-10 -mx-1 px-1 py-2 backdrop-blur lg:sticky lg:top-[3.75rem]">
				<div className="flex items-center gap-3">
					<div className="flex-1">
						<Input
							value={filter}
							onChange={(e) => setFilter(e.target.value)}
							placeholder="Filter operations & models"
							aria-label="Filter API operations"
							startIcon={<Filter className="h-3.5 w-3.5" />}
						/>
					</div>
					{q && (
						<span className="text-foreground/50 shrink-0 text-xs">
							{matchTotal} match{matchTotal === 1 ? '' : 'es'}
						</span>
					)}
				</div>
			</div>

			{empty ? (
				<p className="text-foreground/50 py-6 text-center text-sm">
					No operations or models match your filter.
				</p>
			) : (
				<div className="grid grid-cols-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
					<ReferenceIndex
						parsed={parsed}
						activeId={activeId}
						showModels={parsed.models.length > 0}
						prefix={anchorPrefix}
					/>
					<div className="min-w-0 space-y-10">
						{parsed.groups.map((group) => (
							<section
								key={group.name}
								id={tagGroupAnchorId(group.name, anchorPrefix)}
								className="scroll-mt-28"
							>
								<header className="border-border/60 mb-5 border-b pb-2">
									<div className="flex items-center gap-2.5">
										<span
											className="bg-primary h-5 w-1 rounded-full"
											aria-hidden="true"
										/>
										<h3 className="text-foreground text-lg font-bold tracking-tight">
											{group.name}
										</h3>
										<span className="text-foreground/60 text-xs">
											{group.tags.reduce(
												(n, t) => n + t.operations.length,
												0,
											)}{' '}
											operations
										</span>
									</div>
								</header>
								<div className="space-y-8">
									{group.tags.map((tag) => (
										<TagSection
											key={tag.name}
											tag={tag}
											spec={spec}
											refIndex={refIndex}
											prefix={anchorPrefix}
										/>
									))}
								</div>
							</section>
						))}
						<ModelsSection spec={spec} parsed={parsed} prefix={anchorPrefix} />
					</div>
				</div>
			)}
		</div>
	);
}
