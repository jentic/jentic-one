/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiReferenceResponse } from './ApiReferenceResponse';
/**
 * One governed host with the caller's APIs behind it.
 */
export type GovernedHostResponse = {
    apis: Array<ApiReferenceResponse>;
    host: string;
};

