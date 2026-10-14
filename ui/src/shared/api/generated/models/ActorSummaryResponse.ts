/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActorType } from './ActorType';
/**
 * Single actor entry in the actors list.
 */
export type ActorSummaryResponse = {
    /**
     * False for a disabled user or a non-active agent.
     */
    active: boolean;
    actor_type: ActorType;
    /**
     * When the actor was created (RFC 3339).
     */
    created_at: string;
    /**
     * Actor id (`usr_…` or `agnt_…`).
     */
    id: string;
    /**
     * Display name: a user's full name or an agent's name.
     */
    name: string;
};

