/**
 * specOperations — derive per-operation detail (parameters + security) and the
 * global security-scheme map from a resolved OpenAPI document.
 *
 * The operations list endpoint returns slim rows (method/path/tags only), so to
 * render the same rich Parameters/Auth tables that the Discover module shows we
 * read them out of the resolved OpenAPI spec we already fetch for the spec
 * viewer. Everything here is defensive: the spec is typed `unknown` on this
 * branch and may be partial, so missing fields collapse to empty arrays.
 */
import type { OperationParameter, SecuritySchemeMap } from '@/shared/ui';

/** Detail for one operation, keyed by `METHOD path` (see {@link opDetailKey}). */
export interface SpecOperationDetail {
	parameters: OperationParameter[];
	security: string[];
}

export interface ParsedSpec {
	/** `securitySchemes` (OpenAPI 3.x `components.securitySchemes`). */
	securitySchemes: SecuritySchemeMap;
	/** Map of `METHOD path` → detail. */
	operations: Map<string, SpecOperationDetail>;
}

const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'patch', 'options', 'head', 'trace'];

/** Stable lookup key shared by the parser and the operations rows. */
export function opDetailKey(method: string, path: string): string {
	return `${method.toUpperCase()} ${path}`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
	return value != null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function parseParameters(raw: unknown): OperationParameter[] {
	if (!Array.isArray(raw)) return [];
	const out: OperationParameter[] = [];
	for (const entry of raw) {
		const p = asRecord(entry);
		if (!p || typeof p.name !== 'string' || typeof p.in !== 'string') continue;
		out.push({
			name: p.name,
			in: p.in,
			required: p.required === true,
			description: typeof p.description === 'string' ? p.description : undefined,
		});
	}
	return out;
}

/** Flatten the operation's `security` requirement list to scheme names. */
function parseSecurity(raw: unknown): string[] {
	if (!Array.isArray(raw)) return [];
	const names = new Set<string>();
	for (const requirement of raw) {
		const rec = asRecord(requirement);
		if (!rec) continue;
		for (const name of Object.keys(rec)) names.add(name);
	}
	return Array.from(names);
}

/**
 * Parse a resolved OpenAPI document into a detail lookup. Path-level parameters
 * are merged into each operation (OpenAPI semantics) and operation-level
 * `security` falls back to the document-level default.
 */
export function parseSpecOperations(spec: unknown): ParsedSpec {
	const empty: ParsedSpec = { securitySchemes: {}, operations: new Map() };
	const doc = asRecord(spec);
	if (!doc) return empty;

	const components = asRecord(doc.components);
	const schemesRaw = asRecord(components?.securitySchemes);
	const securitySchemes: SecuritySchemeMap = {};
	if (schemesRaw) {
		for (const [name, value] of Object.entries(schemesRaw)) {
			const rec = asRecord(value);
			if (rec) securitySchemes[name] = rec;
		}
	}

	const defaultSecurity = parseSecurity(doc.security);

	const operations = new Map<string, SpecOperationDetail>();
	const paths = asRecord(doc.paths);
	if (paths) {
		for (const [path, pathItemRaw] of Object.entries(paths)) {
			const pathItem = asRecord(pathItemRaw);
			if (!pathItem) continue;
			const sharedParams = parseParameters(pathItem.parameters);
			for (const method of HTTP_METHODS) {
				const opRaw = asRecord(pathItem[method]);
				if (!opRaw) continue;
				const opParams = parseParameters(opRaw.parameters);
				const merged = [...sharedParams, ...opParams];
				const security =
					opRaw.security !== undefined ? parseSecurity(opRaw.security) : defaultSecurity;
				operations.set(opDetailKey(method, path), {
					parameters: merged,
					security,
				});
			}
		}
	}

	return { securitySchemes, operations };
}

/** How a resolved spec treats authentication — see {@link specAuthRequirement}. */
export type SpecAuthRequirement = 'required' | 'optional' | 'none';

function requirementIsMandatory(raw: unknown): boolean {
	if (!Array.isArray(raw) || raw.length === 0) return false;
	return raw.every((entry) => {
		const rec = asRecord(entry);
		return rec != null && Object.keys(rec).length > 0;
	});
}

function requirementOffersScheme(raw: unknown): boolean {
	if (!Array.isArray(raw)) return false;
	return raw.some((entry) => {
		const rec = asRecord(entry);
		return rec != null && Object.keys(rec).length > 0;
	});
}

/**
 * Whether a resolved OpenAPI document actually REQUIRES authentication, as
 * opposed to merely declaring `securitySchemes`:
 *
 *   - `required` — at least one operation's effective `security` (its own, or
 *     the document default) lists requirements, none of them the empty `{}`
 *     that makes auth optional
 *   - `optional` — schemes are declared or offered, but no operation requires
 *     one (`security: []`, or an empty `{}` alternative everywhere)
 *   - `none` — nothing declared, nothing required
 *
 * `null` when the input isn't an OpenAPI document. Covers OpenAPI 3.x
 * `components.securitySchemes` and Swagger 2 `securityDefinitions`.
 */
export function specAuthRequirement(spec: unknown): SpecAuthRequirement | null {
	const doc = asRecord(spec);
	if (!doc || (doc.openapi === undefined && doc.swagger === undefined)) return null;

	const declared =
		Object.keys(asRecord(asRecord(doc.components)?.securitySchemes) ?? {}).length > 0 ||
		Object.keys(asRecord(doc.securityDefinitions) ?? {}).length > 0;

	const effective: unknown[] = [];
	const paths = asRecord(doc.paths);
	if (paths) {
		for (const pathItemRaw of Object.values(paths)) {
			const pathItem = asRecord(pathItemRaw);
			if (!pathItem) continue;
			for (const method of HTTP_METHODS) {
				const opRaw = asRecord(pathItem[method]);
				if (!opRaw) continue;
				effective.push(opRaw.security !== undefined ? opRaw.security : doc.security);
			}
		}
	}
	// No operations to judge by: the document default is all there is.
	if (effective.length === 0) effective.push(doc.security);

	if (effective.some(requirementIsMandatory)) return 'required';
	if (declared || effective.some(requirementOffersScheme)) return 'optional';
	return 'none';
}
