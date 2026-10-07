/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Type of authenticated actor: a human user or an agent.
 *
 * Historical records may carry older actor-type values that are no longer
 * issued; treat unrecognised values as opaque labels.
 */
export enum ActorType {
    USER = 'user',
    AGENT = 'agent',
}
