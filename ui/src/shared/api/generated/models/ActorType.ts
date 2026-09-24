/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Type of authenticated actor.
 *
 * ``toolkit`` is retired (theme-5 Phase 4): toolkit keys resolve as the
 * agents the key-retirement job created, so no code path mints a
 * toolkit identity. Persisted ``actor_type='toolkit'`` strings survive in
 * historical rows (events, audit entries, execution records) until the
 * Phase-6b scope-data sweep; read paths must tolerate the string without
 * round-tripping it through this enum.
 *
 * ``service_account`` is deserialization-only (theme-8 Phase 2): the
 * service-account surface is gone and no issuance path produces it, but
 * stored token rows, grant rows, audit/execution records, and telemetry
 * history carry the value, and the Phase-1 resolver fallback still resolves
 * unmigrated ``sak_`` keys as it. Deletion is a Phase-4/5 decision.
 */
export enum ActorType {
    USER = 'user',
    AGENT = 'agent',
    SERVICE_ACCOUNT = 'service_account',
}
