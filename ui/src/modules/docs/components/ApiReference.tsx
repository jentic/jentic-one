/**
 * ApiReferenceView — a native, theme-matched OpenAPI reference.
 *
 * The spec drives everything (see lib/apiSpec.parseSpec): we render every tag
 * group, tag, operation, request/response body schema, and the component
 * schemas ("Models") in standard OpenAPI ordering (x-tagGroups → tags →
 * operations). Each operation is enriched with our permission reference (the
 * one thing the spec lacks) joined on `(method,path)`.
 *
 * Layout mirrors the CLI reference: a sticky grouped index on the left + one
 * scrolling document on the right + scroll-spy, so the two reference sections
 * feel like one product.
 */
import { memo, useMemo, useState } from 'react';
import { Filter, Lock, Globe } from 'lucide-react';
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
import { lookupKey, indexReference } from '@/modules/docs/lib/anchor';
import { ApiOverview } from '@/modules/docs/components/ApiOverview';
import {
	MODELS_ANCHOR,
	modelsAnchorId,
	opAnchorId,
	prefixedModelAnchorId,
	tagAnchorId,
	tagGroupAnchorId,
} from '@/modules/docs/components/apiReferenceAnchors';
import { Disclosure, SectionLabel } from '@/modules/docs/components/apiReferenceParts';
import { PermissionPanel } from '@/modules/docs/components/PermissionPanel';
import { ReferenceIndex } from '@/modules/docs/components/ReferenceIndex';
import { SchemaView } from '@/modules/docs/components/SchemaView';
import { useScrollSpy } from '@/modules/docs/lib/useScrollSpy';
import { MethodBadge, Markdown, Input, LazyMount } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

export { MODELS_ANCHOR, tagGroupAnchorId };

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
			<p className="text-foreground-faint mb-1.5 font-mono text-[11px]">{body.contentType}</p>
			{body.schema && Object.keys(body.schema).length > 0 ? (
				<SchemaView spec={spec} schema={body.schema} anchorPrefix={anchorPrefix} />
			) : (
				<p className="text-foreground-faint text-[12px] italic">No schema.</p>
			)}
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
				{p.type && <code className="text-primary font-mono text-[11px]">{p.type}</code>}
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
								className="bg-muted/40 text-foreground-sub rounded px-1 py-px font-mono text-[10px]"
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

/** A compact auth chip for the operation header (public vs. permission-gated). */
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
	const permissions = endpoint.required_permissions ?? [];
	return (
		<span className="text-foreground-sub inline-flex items-center gap-1 text-[11px]">
			<Lock className="h-3 w-3" aria-hidden="true" />
			{permissions.length > 0 ? (
				<code className="font-mono">
					{permissions[0]}
					{permissions.length > 1 ? ` +${permissions.length - 1}` : ''}
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
				<p className="text-foreground-sub mt-0.5 font-mono text-[11px]">
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
					<PermissionPanel endpoint={endpoint} />
				</div>
			)}

			{/* Request: parameters (open — essential) + body (collapsed — heavy). */}
			{(paramGroups.length > 0 || reqBodies.length > 0) && (
				<div className="mt-5">
					<SectionLabel count={op.parameters.length || undefined}>Request</SectionLabel>
					<div className="space-y-3">
						{paramGroups.map(([loc, params]) => (
							<div key={loc} className="border-border/40 rounded-lg border px-3 py-1">
								<p className="text-foreground-faint border-border/30 border-b py-1.5 text-[10px] font-semibold tracking-wider uppercase">
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
										<code className="text-foreground-faint font-mono text-[11px]">
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
										<code className="text-foreground-faint shrink-0 font-mono text-[10px]">
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
						className="text-foreground-sub mt-0.5 text-sm"
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
				<p className="text-foreground-sub text-xs">
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
									className="text-foreground-sub mt-1 text-[13px]"
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
						<span className="text-foreground-sub shrink-0 text-xs">
							{matchTotal} match{matchTotal === 1 ? '' : 'es'}
						</span>
					)}
				</div>
			</div>

			{empty ? (
				<p className="text-foreground-sub py-6 text-center text-sm">
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
										<span className="text-foreground-sub text-xs">
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
