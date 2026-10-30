/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiContext } from './ApiContext';
import type { AuthInstruction } from './AuthInstruction';
import type { InspectLinks } from './InspectLinks';
import type { OperationInputs } from './OperationInputs';
/**
 * Full structural detail for a resolved operation.
 */
export type OperationInspectResult = {
    /**
     * The API the operation belongs to.
     */
    api: ApiContext;
    /**
     * Security schemes the operation accepts.
     */
    auth?: (Array<AuthInstruction> | null);
    /**
     * The operation's description from the spec.
     */
    description?: (string | null);
    /**
     * Parameters and request body the operation accepts.
     */
    inputs?: (OperationInputs | null);
    /**
     * Links for this result.
     */
    links: InspectLinks;
    /**
     * HTTP method of the operation.
     */
    method: string;
    /**
     * The operation's summary from the spec.
     */
    name?: (string | null);
    /**
     * The operation's id in its spec.
     */
    operation_id: string;
    /**
     * The operation's raw spec fragment; only populated for `detail=full`.
     */
    raw_spec?: (Record<string, any> | null);
    /**
     * JSON Schema of the operation's success response, when known.
     */
    response_schema?: (Record<string, any> | null);
    /**
     * Server URL the operation is resolved against.
     */
    server?: (string | null);
    /**
     * Always `operation`.
     */
    type?: string;
    /**
     * Upstream URL of the operation.
     */
    url: string;
};

