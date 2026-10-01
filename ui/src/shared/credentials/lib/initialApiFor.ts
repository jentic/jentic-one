/**
 * The `initialApi` a `CreateCredentialFlow` host passes when it already knows
 * which workspace API the credential is for — the flow then opens on that
 * API's form (step 2) instead of the picker.
 *
 * Shared so every "add a credential for THIS API" entry point (the API hub's
 * "Who can use it", the Library panel's "no credential" item) seeds the
 * flow identically: same identity, same catalog slug (stored on the
 * credential), same scheme hint, same label.
 */
import type { SelectedApi } from '@/shared/credentials/api';

export interface WorkspaceApiSeed {
	/** The registered API's identity (`/apis` row `api`). */
	ref: { vendor: string; name: string; version: string };
	/** Catalog slug recorded at import (`catalog_api_id`), if any. */
	catalogApiId: string | null;
	/** Declared security scheme types (`/apis` row `security_schemes`). */
	securitySchemes: readonly string[];
	/** Human display title — the one the host already shows for this API. */
	label: string;
}

export function initialApiFor(api: WorkspaceApiSeed): SelectedApi {
	return {
		source: 'local',
		vendor: api.ref.vendor,
		name: api.ref.name,
		version: api.ref.version,
		apiId: api.catalogApiId ?? undefined,
		registered: true,
		securitySchemeTypes: [...api.securitySchemes],
		label: api.label,
	};
}
