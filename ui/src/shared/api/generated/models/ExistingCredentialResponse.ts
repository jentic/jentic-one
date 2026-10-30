/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditAgentRef } from './EditAgentRef';
/**
 * A credential the viewer already holds for the target.
 *
 * ``granted_scopes`` / ``missing_scopes`` are set for OAuth credentials
 * only; a static credential's upstream permissions are not visible to the
 * platform. Binding one with ``missing_scopes`` runs a vendor consent for
 * them first (``needs_vendor_consent``), which widens the access of every
 * agent in ``other_bound_agents`` too.
 */
export type ExistingCredentialResponse = {
    /**
     * Whether this viewer may use it: its grant covers the request, or the missing scopes can be consented and the viewer may widen it.
     */
    can_bind: boolean;
    credential_id: string;
    granted_scopes?: (Array<string> | null);
    missing_scopes?: (Array<string> | null);
    name: string;
    /**
     * Binding it first runs a vendor consent for missing_scopes.
     */
    needs_vendor_consent?: boolean;
    /**
     * Other agents bound to the credential; added scopes reach them too.
     */
    other_bound_agents?: Array<EditAgentRef>;
    type: string;
};

