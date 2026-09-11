/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Type of authenticated actor.
 *
 * ``toolkit`` is retired (theme-5 Phase 4): toolkit keys resolve as the
 * service accounts the key-retirement job created, so no code path mints a
 * toolkit identity. Persisted ``actor_type='toolkit'`` strings survive in
 * historical rows (events, audit entries, execution records) until the
 * Phase-6b scope-data sweep; read paths must tolerate the string without
 * round-tripping it through this enum.
 */
export enum ActorType {
    USER = 'user',
    AGENT = 'agent',
    SERVICE_ACCOUNT = 'service_account',
}
