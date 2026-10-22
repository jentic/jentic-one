/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What `jentic run --container` sets for the vendor's tools.
 */
export type CliFacadeContainerWiringResponse = {
    /**
     * Variables to set on the agent container. A value is a literal or `{placeholder}`, the egress proxy's fixed non-secret placeholder token.
     */
    env_set: Record<string, string>;
    /**
     * Variables removed from the agent container's environment.
     */
    env_strip: Array<string>;
    /**
     * Hosts the vendor's tools need reached directly (tunnelled by the egress proxy, never decrypted).
     */
    tunnel_hosts: Array<string>;
};

