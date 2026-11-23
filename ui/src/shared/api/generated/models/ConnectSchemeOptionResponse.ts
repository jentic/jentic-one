/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectScopeOptionResponse } from './ConnectScopeOptionResponse';
/**
 * How a connection would collect one of the API's declared security schemes.
 */
export type ConnectSchemeOptionResponse = {
    /**
     * What a session would run: an OAuth flow (`authorization_code`, `device_authorization`), a `manual_*` entry, or `awaiting_app` while no OAuth app covers the API.
     */
    flow: (string | null);
    /**
     * What a connect session collects for it; null when it can't serve it.
     */
    kind: ('oauth2' | 'api_key' | 'bearer' | 'basic' | null);
    /**
     * The scheme's name in the spec.
     */
    name: string;
    /**
     * The shared OAuth app it would use, when that's the source.
     */
    registration_id?: (string | null);
    scopes?: Array<ConnectScopeOptionResponse>;
    /**
     * The spec's scheme type.
     */
    type: string;
    /**
     * The OAuth app's vendor key, when one covers the API.
     */
    vendor_key?: (string | null);
};

