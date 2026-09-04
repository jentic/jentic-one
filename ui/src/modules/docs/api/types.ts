/**
 * Types for the canonical endpoint reference served at
 * `GET /reference/endpoints.json` (built by
 * src/jentic_one/shared/web/endpoint_reference.py — schema
 * `jentic.endpoint-scope-tree/v1`).
 *
 * This is the docs SPA's source of truth for the per-endpoint authorization
 * model (required scopes, the advisory "typical caller" hint, and any
 * non-standard auth note). It is deliberately NOT in the OpenAPI document
 * (#602). The SPA fetches it separately and uses
 * it to enrich each operation in the native API reference — it never mutates
 * the spec.
 */

/** A single endpoint row from the reference payload. */
export interface ReferenceEndpoint {
	method: string;
	path: string;
	surface: string;
	summary: string;
	operation_id: string | null;
	authenticated: boolean;
	public: boolean;
	actor_types: string[];
	required_scopes: string[];
	/** scope -> the scopes it implies (transitive closure), if any. */
	implied_scopes: Record<string, string[]>;
	auth_note: string | null;
	/** Advisory hint ("agent" | "operator" | "any"); NOT an enforced gate. */
	typical_caller: string | null;
	/** Display group (added by the backend builder). */
	group: string;
}

/** The full `/reference/endpoints.json` payload. */
export interface ReferencePayload {
	schema: string;
	total: number;
	groups: string[];
	endpoints: ReferenceEndpoint[];
	/** Conceptual scope catalogue (meaning + implication graph). May be absent on
	 * a server that predates the catalogue (older #602 build). */
	scopes?: ScopeCatalog;
}

/** One scope's conceptual entry (meaning + relationships). */
export interface ScopeEntry {
	name: string;
	description: string;
	/** Resource family prefix, e.g. `agents`, `credentials`, `owner`. */
	family: string;
	/** Action suffix, e.g. `read` | `write` | `execute` | `admin`. */
	action: string;
	/** Direct (one-hop) child scopes this scope implies. */
	implies: string[];
	/** Full transitive closure of implied scopes (sorted). */
	implies_transitive: string[];
	/** `org:admin` is also a hard runtime superpower (bypasses endpoint checks). */
	is_superuser: boolean;
}

/** A family (resource prefix) with its scopes, for the grouped scope tree. */
export interface ScopeFamily {
	name: string;
	label: string;
	blurb: string;
	scopes: ScopeEntry[];
}

/** The `scopes` section of the reference payload. */
export interface ScopeCatalog {
	schema: string;
	total: number;
	families: ScopeFamily[];
	scopes: ScopeEntry[];
}

/**
 * The OpenAPI document, loosely typed. We render it natively and only ever read
 * from it (never mutate), so the loose shape is intentional.
 */
export type OpenApiDocument = Record<string, unknown>;

// --- CLI reference (ui/public/cli-reference.json, generated from cobra) -------

/** One flag of a CLI command. */
export interface CliFlag {
	name: string;
	shorthand?: string;
	type: string;
	default?: string;
	usage: string;
}

/** One CLI command (or subcommand) node. */
export interface CliCommand {
	name: string;
	/** Full invocation path, e.g. `jentic profile add-key`. */
	path: string;
	/** cobra usage line (carries the positional-arg shape). */
	use: string;
	short: string;
	long?: string;
	example?: string;
	aliases?: string[];
	/** Human title of the command group it belongs to. */
	group_title?: string;
	flags?: CliFlag[];
	subcommands?: CliCommand[];
}

/** One CLI binary (`jentic` / `jenticctl`) and its command tree. */
export interface CliBinary {
	name: string;
	tagline?: string;
	short: string;
	long?: string;
	commands: CliCommand[];
}

/** The full `cli-reference.json` payload. */
export interface CliReference {
	schema: string;
	binaries: CliBinary[];
}
