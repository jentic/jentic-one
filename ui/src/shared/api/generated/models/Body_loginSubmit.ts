/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type Body_loginSubmit = {
    /**
     * Single-use CSRF token embedded in the login form.
     */
    csrf: string;
    /**
     * Email address of the local account.
     */
    email: string;
    /**
     * Signed authorization-flow state (carry-through token) from the login form.
     */
    ls: string;
    /**
     * Password of the local account.
     */
    password: string;
};

