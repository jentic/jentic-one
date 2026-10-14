/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Payload for redeeming an invite token.
 */
export type RedeemInviteRequest = {
    /**
     * The invite token issued when the user was created or re-invited.
     */
    invite_token: string;
    /**
     * Password to set for the invited user.
     */
    password: string;
};

