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
     * Shape version of this document, bumped only when a field is removed, renamed, or retyped. New keys appear without a bump — ignore unknown keys; hard-fail only on a version you do not understand.
     */
    capabilities_version: number;
    /**
     * Deployment feature flags. OSS ships 'mcp'; downstream packages may contribute additional boolean flags (additive — never overriding built-ins).
     */
    features: Record<string, boolean>;
    instance: CapabilitiesInstanceResponse;
    /**
     * The control-plane surfaces served by the process answering this request (sorted), e.g. ['admin', 'auth', 'control', 'registry']. On a split deployment each tier reports only its own surfaces — a capability absent here may be served by a sibling tier.
     */
    surfaces: Array<string>;
    urls: CapabilitiesUrlsResponse;
};

