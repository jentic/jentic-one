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
 * ``service_account`` is retired the same way (theme-8 Phase 4 dropped the
 * service-account tables and deleted the member). Historical audit rows,
 * execution records, telemetry history and control-DB actor-id columns
 * (``connect_sessions.initiator_actor_id``, ``credentials.created_by``)
 * still carry the string or ``sva_`` ids; read paths use
 * :func:`actor_type_label_from_id` or treat the string as opaque.
 */
export enum ActorType {
    USER = 'user',
    AGENT = 'agent',
}
