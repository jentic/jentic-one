/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditAgentRef } from './EditAgentRef';
import type { EditBindingRef } from './EditBindingRef';
import type { EditConflict } from './EditConflict';
import type { EditCurrentAccess } from './EditCurrentAccess';
import type { EditResultAccess } from './EditResultAccess';
import type { SessionRevisionRef } from './SessionRevisionRef';
/**
 * The binding change an edit session asks a human to approve.
 */
export type ReviewEditResponse = {
    binding: EditBindingRef;
    /**
     * What the request can't follow since the binding changed under it. While any are listed, a confirm must set `resolve_conflicts`.
     */
    conflicts?: Array<EditConflict>;
    current: EditCurrentAccess;
    /**
     * True when the result adds scopes, so confirming starts a vendor consent.
     */
    needs_vendor_consent?: boolean;
    /**
     * Other agents bound to the credential; added scopes and a shared rule set change reach them.
     */
    other_bound_agents?: Array<EditAgentRef>;
    result: EditResultAccess;
    revision: SessionRevisionRef;
    /**
     * True when the change could let the agent do more than today.
     */
    widens_access: boolean;
};

