/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A non-fatal signal about a bind (or create-time inline bind).
 */
export type BindingWarningSchema = {
    /**
     * Stable machine-readable warning code.
     */
    code: string;
    /**
     * Credential the warning applies to; null when the whole binding is meant.
     */
    credential_id?: (string | null);
    /**
     * Human-readable explanation with a recovery pointer.
     */
    message: string;
};

