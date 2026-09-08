/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Revocation endpoint request (RFC 7009) — JSON or form-encoded.
 *
 * ``token`` is required either way. ``token_type_hint``
 * (``access_token``/``refresh_token``) is a lookup-order optimization only —
 * the server falls through both types regardless (RFC 7009 §2.1).
 * ``client_id`` belongs to the form-encoded public-client arm (G11): the
 * secret-less client's lineage binding; ignored on the bearer-authenticated
 * JSON arm, where the platform identity scopes the revocation instead.
 */
export type RevokeRequest = {
    client_id?: (string | null);
    token: string;
    token_type_hint?: (string | null);
};

