/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A change to the binding's OAuth credential scopes: `add`/`remove`, or the full list in `set`.
 *
 * Confirming a change runs a vendor sign-in for exactly the resulting scopes;
 * other agents bound to the credential get the change too.
 */
export type EditScopesRequest = {
    /**
     * Scopes to add (at most 100).
     */
    add?: Array<string>;
    /**
     * Why each scope is asked for (added or removed), by scope name. Shown to the reviewer.
     */
    comments?: Record<string, string>;
    /**
     * Scopes to remove (at most 100).
     */
    remove?: Array<string>;
    /**
     * The full scope list to replace the grant's with. Not combined with add or remove.
     */
    set?: (Array<string> | null);
};

