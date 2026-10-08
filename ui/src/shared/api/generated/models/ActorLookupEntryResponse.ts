/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorType } from './ActorType';
/**
 * Display fields of one actor resolved by id.
 */
export type ActorLookupEntryResponse = {
    /**
     * False for a disabled user or a non-active agent.
     */
    active: boolean;
    /**
     * Whether the actor is a user or an agent.
     */
    actor_type: ActorType;
    /**
     * Actor id (`usr_…` or `agnt_…`).
     */
    id: string;
    /**
     * Display name: a user's full name or an agent's name.
     */
    name: string;
};

