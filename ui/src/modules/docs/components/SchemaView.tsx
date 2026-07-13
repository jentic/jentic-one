/**
 * SchemaView — a compact, recursive JSON Schema renderer for the API reference.
 *
 * Renders an OpenAPI/JSON-Schema node as an indented property tree: each
 * property shows its name, type, required marker, constraints (enum, format,
 * nullable, default) and description. `$ref`s render as a link to the model in
 * the Models section, and expand inline one level so the reader sees the shape
 * without leaving the operation. Recursion is depth-bounded to stay readable
 * and avoid cycles.
 */
import { useId, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import type { OpenApiDocument } from '@/modules/docs/api/types';
import { derefSchema, refName } from '@/modules/docs/lib/apiSpec';
import { modelAnchorId, scrollToAnchor } from '@/modules/docs/lib/anchor';
import { cn } from '@/shared/lib/utils';
import { Markdown } from '@/shared/ui';

export { modelAnchorId };

/**
 * A link to a model in the Models section. Uses scrollToAnchor (not a raw hash
 * jump) so it lands precisely even though models are lazily mounted and sit
 * below sticky headers — same navigation path as the sidebar/search.
 */
function ModelLink({
	name,
	suffix = '',
	anchorPrefix = '',
}: {
	name: string;
	suffix?: string;
	anchorPrefix?: string;
}) {
	const base = modelAnchorId(name);
	const id = anchorPrefix ? `${anchorPrefix}-${base}` : base;
	return (
		<a
			href={`#${id}`}
			onClick={(e) => {
				e.preventDefault();
				scrollToAnchor(id);
				history.replaceState(null, '', `#${id}`);
			}}
			className="text-primary inline-block font-mono text-[12px] hover:underline"
		>
			{name}
			{suffix}
		</a>
	);
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

/** A short human type label for a schema node (e.g. `string`, `Foo[]`, `Foo`). */
function typeLabel(spec: OpenApiDocument, node: unknown): string {
	if (!isObj(node)) return 'any';
	const named = refName(node);
	if (named) return named;
	if (Array.isArray(node.oneOf) || Array.isArray(node.anyOf)) {
		const variants = (node.oneOf ?? node.anyOf) as unknown[];
		const labels = variants.map((v) => typeLabel(spec, v));
		return [...new Set(labels)].join(' | ') || 'any';
	}
	if (Array.isArray(node.allOf)) return 'object';
	const t = node.type;
	if (t === 'array') {
		const items = node.items;
		return `${typeLabel(spec, items)}[]`;
	}
	// Map-shaped object: `{type: object, additionalProperties: <schema>}`.
	if (
		(t === 'object' || t === undefined) &&
		isObj(node.additionalProperties) &&
		!isObj(node.properties)
	) {
		return `map<string, ${typeLabel(spec, node.additionalProperties)}>`;
	}
	if (typeof t === 'string') {
		if (typeof node.format === 'string') return `${t}<${node.format}>`;
		return t;
	}
	if (Array.isArray(t)) return t.join(' | ');
	if (isObj(node.properties)) return 'object';
	return 'any';
}

function constraintBadges(node: Obj): string[] {
	const out: string[] = [];
	if (Array.isArray(node.enum)) {
		const vals = node.enum.map((v) => JSON.stringify(v)).join(', ');
		out.push(`enum: ${vals}`);
	}
	if (node.default !== undefined) out.push(`default: ${JSON.stringify(node.default)}`);
	if (node.nullable === true) out.push('nullable');
	if (typeof node.minimum === 'number') out.push(`min ${node.minimum}`);
	if (typeof node.maximum === 'number') out.push(`max ${node.maximum}`);
	if (typeof node.minLength === 'number') out.push(`minLen ${node.minLength}`);
	if (typeof node.maxLength === 'number') out.push(`maxLen ${node.maxLength}`);
	if (typeof node.pattern === 'string') out.push(`pattern ${node.pattern}`);
	return out;
}

/** Merge an `allOf` chain into a single {properties, required} view. */
function flattenAllOf(
	spec: OpenApiDocument,
	node: Obj,
): {
	properties: Record<string, unknown>;
	required: Set<string>;
} {
	const properties: Record<string, unknown> = {};
	const required = new Set<string>();
	// Guard against circular `allOf`/`$ref` chains (e.g. `A.allOf: [A]`, a
	// mutual `A→B→A` cycle, or a self-referential `$ref`), which are valid
	// OpenAPI shapes for recursive models. Without this the walk recurses
	// forever and overflows the stack, blanking the whole reference render.
	// Track the `$ref` string (pre-deref) and resolved object identity
	// (post-deref) so both ref cycles and inline cycles are caught.
	const seenRefs = new Set<string>();
	const seenNodes = new WeakSet<object>();
	const visit = (n: unknown) => {
		if (isObj(n) && typeof n.$ref === 'string') {
			if (seenRefs.has(n.$ref)) return;
			seenRefs.add(n.$ref);
		}
		const { schema } = derefSchema(spec, n);
		if (!isObj(schema)) return;
		if (seenNodes.has(schema)) return;
		seenNodes.add(schema);
		if (Array.isArray(schema.allOf)) schema.allOf.forEach(visit);
		if (isObj(schema.properties)) Object.assign(properties, schema.properties);
		if (Array.isArray(schema.required))
			for (const r of schema.required) if (typeof r === 'string') required.add(r);
	};
	visit(node);
	return { properties, required };
}

interface SchemaNodeProps {
	spec: OpenApiDocument;
	node: unknown;
	depth: number;
	/** When true this node is a `$ref` we've already labelled; show a link not a tree. */
	asRefLink?: boolean;
	/** Namespace for model anchor links (multi-instance pages). */
	anchorPrefix?: string;
}

const MAX_DEPTH = 4;

function SchemaNode({ spec, node, depth, anchorPrefix = '' }: SchemaNodeProps) {
	const named = refName(node);
	const { schema } = derefSchema(spec, node);

	if (!isObj(schema)) {
		return <p className="text-foreground/50 text-[13px]">any</p>;
	}

	// A named ref: link to the model, and (shallowly) expand its object shape.
	const resolved = isObj(schema) ? schema : {};
	const isArray = resolved.type === 'array';
	const itemsNamed = isArray ? refName(resolved.items) : undefined;

	// Composition (oneOf/anyOf): list each variant; link named ones to models.
	const variants = (
		Array.isArray(resolved.oneOf)
			? resolved.oneOf
			: Array.isArray(resolved.anyOf)
				? resolved.anyOf
				: null
	) as unknown[] | null;

	const { properties, required } = flattenAllOf(spec, resolved);
	const propEntries = Object.entries(properties);

	return (
		<div className="space-y-1">
			{named && <ModelLink name={named} anchorPrefix={anchorPrefix} />}
			{isArray && itemsNamed && (
				<ModelLink name={itemsNamed} suffix="[]" anchorPrefix={anchorPrefix} />
			)}

			{!named && variants && variants.length > 0 ? (
				<div className="space-y-1">
					<p className="text-foreground/45 text-[11px]">
						{Array.isArray(resolved.oneOf) ? 'One of' : 'Any of'}:
					</p>
					<ul className="flex flex-wrap items-center gap-1.5">
						{variants.map((v, i) => {
							const vn = refName(v);
							return (
								<li key={vn ?? i} className="flex items-center gap-1.5">
									{i > 0 && (
										<span className="text-foreground/30 text-[11px]">|</span>
									)}
									{vn ? (
										<ModelLink name={vn} anchorPrefix={anchorPrefix} />
									) : (
										<code className="text-foreground/55 font-mono text-[12px]">
											{typeLabel(spec, v)}
										</code>
									)}
								</li>
							);
						})}
					</ul>
				</div>
			) : propEntries.length > 0 && depth < MAX_DEPTH ? (
				<ul className="border-border/40 space-y-1.5 border-l pl-3">
					{propEntries.map(([name, propRaw]) => (
						<PropertyRow
							key={name}
							spec={spec}
							name={name}
							node={propRaw}
							required={required.has(name)}
							depth={depth}
							anchorPrefix={anchorPrefix}
						/>
					))}
				</ul>
			) : (
				!named &&
				!itemsNamed && (
					<p className="text-foreground/55 font-mono text-[12px]">
						{typeLabel(spec, resolved)}
					</p>
				)
			)}
		</div>
	);
}

function PropertyRow({
	spec,
	name,
	node,
	required,
	depth,
	anchorPrefix = '',
}: {
	spec: OpenApiDocument;
	name: string;
	node: unknown;
	required: boolean;
	depth: number;
	anchorPrefix?: string;
}) {
	const { schema } = derefSchema(spec, node);
	const obj = isObj(schema) ? schema : {};
	const named = refName(node);
	const nestedProps =
		!named && (isObj(obj.properties) || obj.type === 'array' || Array.isArray(obj.allOf));
	const itemNamed = obj.type === 'array' ? refName(obj.items) : undefined;
	const canExpand = (nestedProps || itemNamed) && depth + 1 < MAX_DEPTH;
	const [open, setOpen] = useState(false);
	const regionId = useId();
	const badges = isObj(obj) ? constraintBadges(obj) : [];
	const desc = isObj(obj) ? (obj.description as string | undefined) : undefined;

	return (
		<li>
			<div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
				{canExpand ? (
					<button
						type="button"
						onClick={() => setOpen((v) => !v)}
						aria-expanded={open}
						aria-controls={regionId}
						className="text-foreground hover:text-primary inline-flex items-center gap-1 font-mono text-[13px]"
					>
						<ChevronRight
							aria-hidden="true"
							className={cn('h-3 w-3 transition-transform', open && 'rotate-90')}
						/>
						{name}
					</button>
				) : (
					<code className="text-foreground font-mono text-[13px]">{name}</code>
				)}
				<span className="text-primary/80 font-mono text-[11px]">
					{typeLabel(spec, schema)}
				</span>
				{required && <span className="text-danger text-[10px] font-medium">required</span>}
				{badges.map((b) => (
					<span
						key={b}
						className="bg-muted/40 text-foreground/55 rounded px-1 py-px font-mono text-[10px]"
					>
						{b}
					</span>
				))}
			</div>
			{desc && (
				<Markdown
					source={desc}
					className="text-foreground/55 mt-0.5 text-[12px] leading-snug"
				/>
			)}
			{canExpand && open && (
				<div id={regionId} className="mt-1.5">
					<SchemaNode
						spec={spec}
						node={node}
						depth={depth + 1}
						anchorPrefix={anchorPrefix}
					/>
				</div>
			)}
		</li>
	);
}

export interface SchemaViewProps {
	spec: OpenApiDocument;
	schema: unknown;
	/** Namespace for model anchor links when multiple references share a page. */
	anchorPrefix?: string;
}

/** Render a request/response/model schema as a property tree. */
export function SchemaView({ spec, schema, anchorPrefix = '' }: SchemaViewProps) {
	return <SchemaNode spec={spec} node={schema} depth={0} anchorPrefix={anchorPrefix} />;
}
