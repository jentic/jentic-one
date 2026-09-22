/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Identity response for a user actor.
 */
export type MeUser = {
    admin: boolean;
    email: string;
    id: string;
    must_change_password: boolean;
    name: string;
    permissions: Array<string>;
    status: string;
    type?: string;
};

