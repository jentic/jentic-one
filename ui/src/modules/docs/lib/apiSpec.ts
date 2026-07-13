/**
 * apiSpec — parse an OpenAPI document into the structured model our native API
 * reference renders. The spec is the source of truth: we render *everything*
 * that's in it (tags, tag groups, every operation, request/response bodies, and
 * the component schemas / "Models"), then enrich each operation with our scope
 * reference (the one thing the spec doesn't carry) by joining on `(method,path)`.
 *
 * Ordering is the standard OpenAPI/Redoc convention:
 *   x-tagGroups (top-level groups) → tags (in each group's declared order) →
 *   operations (in document order within the tag).
 * Tags not in any group fall into a trailing synthetic group; a "Models" group
 * is appended for the component schemas.
 *
 * Everything narrows defensively — a sparse or non-standard spec just yields a
 * sparser model rather than throwing.
 */
import type { OpenApiDocument } from '@/modules/docs/api/types';
import { lookupKey } from '@/modules/docs/lib/anchor';

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'] as const;

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
	return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | undefined {
	return typeof v === 'string' ? v : undefined;
}

/** A media-type body (request or response) with its resolved schema node. */
export interface SpecBody {
	contentType: string;
	/** The (unresolved) schema node; resolve lazily in the view via `derefSchema`. */
	schema: unknown;
}

/** A request parameter, enriched with a type label + constraint chips. */
export interface SpecParameter {
	name: string;
	in: string;
	required: boolean;
	description?: string;
	/** Short human type label (e.g. `string`, `integer`, `string[]`). */
	type?: string;
	/** Constraint chips (e.g. `enum: …`, `default: …`, `format uuid`). */
	constraints: string[];
}

export interface SpecResponse {
	status: string;
	description: string;
	bodies: SpecBody[];
}

/** A fully-parsed operation, ready to render. */
export interface SpecOperation {
	method: string;
	path: string;
	operationId?: string;
	summary?: string;
	description?: string;
	deprecated: boolean;
	tags: string[];
	parameters: SpecParameter[];
	/** Security scheme names referenced by the operation (or spec default). */
	security: string[];
	requestBodies: SpecBody[];
	requestRequired: boolean;
	responses: SpecResponse[];
}

/** A tag and the operations under it, in document order. */
export interface SpecTag {
	name: string;
	description?: string;
	operations: SpecOperation[];
}

/** A top-level group (from x-tagGroups, or synthetic) and its tags. */
export interface SpecTagGroup {
	name: string;
	tags: SpecTag[];
}

/** One named component schema (a "Model"). */
export interface SpecModel {
	name: string;
	schema: unknown;
}

/** A server entry (base URL) from the document's `servers`. */
export interface SpecServer {
	url: string;
	description?: string;
}

/** Contact / license info from `info`, surfaced in the reference header. */
export interface SpecInfoMeta {
	contactName?: string;
	contactUrl?: string;
	contactEmail?: string;
	licenseName?: string;
	licenseUrl?: string;
	termsOfService?: string;
}

export interface ParsedSpec {
	title?: string;
	version?: string;
	description?: string;
	/** `info.summary` (short tagline above the description). */
	summary?: string;
	/** Base URLs the API is served from (`servers`). */
	servers: SpecServer[];
	/** Contact + license metadata (`info.contact` / `info.license`). */
	meta: SpecInfoMeta;
	groups: SpecTagGroup[];
	models: SpecModel[];
	securitySchemes: Record<string, Record<string, unknown>>;
	/** Raw `components.schemas` for `$ref` resolution by the schema view. */
	schemas: Record<string, unknown>;
}

/** Resolve a local `#/...` ref one hop against the spec; returns node or null. */
function refTarget(spec: OpenApiDocument, ref: string): unknown {
	if (!ref.startsWith('#/')) return null;
	let cur: unknown = spec;
	for (const seg of ref.slice(2).split('/')) {
		if (!isObj(cur)) return null;
		cur = cur[seg.replace(/~1/g, '/').replace(/~0/g, '~')];
	}
	return cur ?? null;
}

/** Resolve a node if it's a `$ref`, else return it unchanged. */
function deref(spec: OpenApiDocument, node: unknown): unknown {
	if (isObj(node) && typeof node.$ref === 'string') return refTarget(spec, node.$ref);
	return node;
}

/** The short model name from a `#/components/schemas/Name` ref, if any. */
export function refName(node: unknown): string | undefined {
	if (isObj(node) && typeof node.$ref === 'string') {
		const parts = node.$ref.split('/');
		return parts[parts.length - 1];
	}
	return undefined;
}

/** A short type label for a parameter/schema node (`string`, `integer[]`, …). */
function schemaTypeLabel(spec: OpenApiDocument, node: unknown): string {
	const resolved = deref(spec, node);
	if (!isObj(resolved)) return 'any';
	const named = refName(node);
	if (named) return named;
	const t = resolved.type;
	if (t === 'array') return `${schemaTypeLabel(spec, resolved.items)}[]`;
	// Map-shaped object: `{type: object, additionalProperties: <schema>}`.
	if (
		(t === 'object' || t === undefined) &&
		isObj(resolved.additionalProperties) &&
		!isObj(resolved.properties)
	) {
		return `map<string, ${schemaTypeLabel(spec, resolved.additionalProperties)}>`;
	}
	if (typeof t === 'string') return t;
	if (Array.isArray(t)) return t.join(' | ');
	if (Array.isArray(resolved.anyOf) || Array.isArray(resolved.oneOf)) {
		const variants = (resolved.anyOf ?? resolved.oneOf) as unknown[];
		return [...new Set(variants.map((v) => schemaTypeLabel(spec, v)))].join(' | ') || 'any';
	}
	return 'any';
}

/** Constraint chips for a parameter/schema node (enum, default, format, …). */
function schemaConstraints(node: unknown): string[] {
	if (!isObj(node)) return [];
	const out: string[] = [];
	if (Array.isArray(node.enum))
		out.push(`enum: ${node.enum.map((v) => JSON.stringify(v)).join(', ')}`);
	if (node.default !== undefined) out.push(`default: ${JSON.stringify(node.default)}`);
	if (node.nullable === true) out.push('nullable');
	if (typeof node.format === 'string') out.push(`format ${node.format}`);
	if (typeof node.minimum === 'number') out.push(`min ${node.minimum}`);
	if (typeof node.maximum === 'number') out.push(`max ${node.maximum}`);
	if (typeof node.minLength === 'number') out.push(`minLen ${node.minLength}`);
	if (typeof node.maxLength === 'number') out.push(`maxLen ${node.maxLength}`);
	if (typeof node.pattern === 'string') out.push(`pattern ${node.pattern}`);
	return out;
}

function readParameters(spec: OpenApiDocument, raw: unknown): SpecParameter[] {
	if (!Array.isArray(raw)) return [];
	const out: SpecParameter[] = [];
	for (const item of raw) {
		const p = deref(spec, item);
		if (!isObj(p)) continue;
		const name = str(p.name);
		if (!name) continue;
		const schema = isObj(p.schema) ? p.schema : undefined;
		out.push({
			name,
			in: str(p.in) ?? '',
			required: p.required === true,
			description: str(p.description),
			type: schema ? schemaTypeLabel(spec, schema) : undefined,
			constraints: schema ? schemaConstraints(schema) : [],
		});
	}
	return out;
}

function readBodies(spec: OpenApiDocument, bodyNode: unknown): SpecBody[] {
	const body = deref(spec, bodyNode);
	if (!isObj(body) || !isObj(body.content)) return [];
	return Object.entries(body.content).map(([contentType, media]) => ({
		contentType,
		schema: isObj(media) ? (media.schema ?? null) : null,
	}));
}

function readResponses(spec: OpenApiDocument, raw: unknown): SpecResponse[] {
	if (!isObj(raw)) return [];
	return Object.entries(raw)
		.map(([status, bodyNodeRaw]) => {
			const body = deref(spec, bodyNodeRaw);
			return {
				status,
				description: isObj(body) ? (str(body.description) ?? '') : '',
				bodies:
					isObj(body) && isObj(body.content)
						? Object.entries(body.content).map(([contentType, media]) => ({
								contentType,
								schema: isObj(media) ? (media.schema ?? null) : null,
							}))
						: [],
			};
		})
		.sort((a, b) => a.status.localeCompare(b.status));
}

function readSecurity(op: Obj, specDefault: string[]): string[] {
	const raw = op.security;
	if (!Array.isArray(raw)) return specDefault;
	const names = new Set<string>();
	for (const entry of raw) {
		if (isObj(entry)) for (const k of Object.keys(entry)) names.add(k);
	}
	return [...names];
}

const UNTAGGED = 'Other';

/** Parse the whole document into the render model. */
export function parseSpec(spec: OpenApiDocument): ParsedSpec {
	const info = isObj(spec.info) ? spec.info : {};
	const components = isObj(spec.components) ? spec.components : {};
	const schemas = isObj(components.schemas)
		? (components.schemas as Record<string, unknown>)
		: {};
	const securitySchemes = isObj(components.securitySchemes)
		? (components.securitySchemes as Record<string, Record<string, unknown>>)
		: {};

	const specSecurity: string[] = (() => {
		const names = new Set<string>();
		if (Array.isArray(spec.security)) {
			for (const entry of spec.security) {
				if (isObj(entry)) for (const k of Object.keys(entry)) names.add(k);
			}
		}
		return [...names];
	})();

	// Tag descriptions, keyed by name.
	const tagDesc = new Map<string, string>();
	if (Array.isArray(spec.tags)) {
		for (const t of spec.tags) {
			if (isObj(t) && str(t.name)) tagDesc.set(str(t.name)!, str(t.description) ?? '');
		}
	}

	// Operations grouped by tag, in document order.
	const opsByTag = new Map<string, SpecOperation[]>();
	const paths = isObj(spec.paths) ? spec.paths : {};
	for (const [path, pathItemRaw] of Object.entries(paths)) {
		const pathItem = deref(spec, pathItemRaw);
		if (!isObj(pathItem)) continue;
		const sharedParams = readParameters(spec, pathItem.parameters);

		for (const method of HTTP_METHODS) {
			const opRaw = pathItem[method];
			if (!isObj(opRaw)) continue;
			const tags = Array.isArray(opRaw.tags)
				? opRaw.tags.filter((t): t is string => typeof t === 'string')
				: [];
			const op: SpecOperation = {
				method: method.toUpperCase(),
				path,
				operationId: str(opRaw.operationId),
				summary: str(opRaw.summary),
				description: str(opRaw.description),
				deprecated: opRaw.deprecated === true,
				tags,
				parameters: [...sharedParams, ...readParameters(spec, opRaw.parameters)],
				security: readSecurity(opRaw, specSecurity),
				requestBodies: readBodies(spec, opRaw.requestBody),
				requestRequired:
					isObj(deref(spec, opRaw.requestBody)) &&
					(deref(spec, opRaw.requestBody) as Obj).required === true,
				responses: readResponses(spec, opRaw.responses),
			};
			const keys = tags.length > 0 ? tags : [UNTAGGED];
			for (const tag of keys) {
				const list = opsByTag.get(tag);
				if (list) list.push(op);
				else opsByTag.set(tag, [op]);
			}
		}
	}

	const usedTags = new Set<string>();
	const makeTag = (name: string): SpecTag => {
		usedTags.add(name);
		return {
			name,
			description: tagDesc.get(name),
			operations: opsByTag.get(name) ?? [],
		};
	};

	const groups: SpecTagGroup[] = [];

	// Declared tag groups first (standard ordering).
	if (Array.isArray(spec['x-tagGroups'])) {
		for (const g of spec['x-tagGroups']) {
			if (!isObj(g)) continue;
			const name = str(g.name);
			if (!name) continue;
			const tagNames = Array.isArray(g.tags)
				? g.tags.filter((t): t is string => typeof t === 'string')
				: [];
			const tags = tagNames.filter((t) => opsByTag.has(t)).map(makeTag);
			if (tags.length > 0) groups.push({ name, tags });
		}
	}

	// Any tags with operations not placed in a group → trailing group.
	const leftover = [...opsByTag.keys()].filter((t) => !usedTags.has(t));
	if (leftover.length > 0) {
		groups.push({
			name: groups.length > 0 ? 'Other' : 'Endpoints',
			tags: leftover.map(makeTag),
		});
	}

	const models: SpecModel[] = Object.entries(schemas)
		.map(([name, schema]) => ({ name, schema }))
		.sort((a, b) => a.name.localeCompare(b.name));

	const servers: SpecServer[] = Array.isArray(spec.servers)
		? spec.servers
				.filter(isObj)
				.map((s) => ({ url: str(s.url) ?? '', description: str(s.description) }))
				.filter((s) => s.url)
		: [];

	const contact = isObj(info.contact) ? info.contact : {};
	const license = isObj(info.license) ? info.license : {};
	const meta: SpecInfoMeta = {
		contactName: str(contact.name),
		contactUrl: str(contact.url),
		contactEmail: str(contact.email),
		licenseName: str(license.name),
		licenseUrl: str(license.url),
		termsOfService: str(info.termsOfService),
	};

	return {
		title: str(info.title),
		version: str(info.version),
		description: str(info.description),
		summary: str(info.summary),
		servers,
		meta,
		groups,
		models,
		securitySchemes,
		schemas,
	};
}

/** Resolve a schema `$ref` one hop for the schema view; returns node + name. */
export function derefSchema(
	spec: OpenApiDocument,
	node: unknown,
): { schema: unknown; name?: string } {
	const name = refName(node);
	if (name) return { schema: deref(spec, node), name };
	return { schema: node };
}

/** Build a `(method,path)` → parsed operation index (for joining elsewhere). */
export function indexParsedOperations(parsed: ParsedSpec): Map<string, SpecOperation> {
	const index = new Map<string, SpecOperation>();
	for (const g of parsed.groups) {
		for (const t of g.tags) {
			for (const op of t.operations) index.set(lookupKey(op.method, op.path), op);
		}
	}
	return index;
}
