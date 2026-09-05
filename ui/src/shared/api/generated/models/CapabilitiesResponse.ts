/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CapabilitiesAuthResponse } from './CapabilitiesAuthResponse';
import type { CapabilitiesInstanceResponse } from './CapabilitiesInstanceResponse';
import type { CapabilitiesUrlsResponse } from './CapabilitiesUrlsResponse';
/**
 * Deployment self-description for one-URL client onboarding.
 *
 * Additive contract: clients must ignore unknown keys (``features`` grows via
 * downstream contributions) and hard-fail only on an unknown
 * ``capabilities_version``.
 */
export type CapabilitiesResponse = {
    auth: CapabilitiesAuthResponse;
    /**
     * Shape version of this document; clients hard-fail on versions they do not understand.
     */
    capabilities_version: number;
    /**
     * Deployment feature flags. OSS ships 'mcp'; downstream packages may contribute additional keys (additive — never overriding built-ins).
     */
    features: Record<string, any>;
    instance: CapabilitiesInstanceResponse;
    /**
     * The control-plane surfaces this deployment serves (sorted), e.g. ['admin', 'auth', 'control', 'registry'].
     */
    surfaces: Array<string>;
    urls: CapabilitiesUrlsResponse;
};

