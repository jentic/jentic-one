/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Authoritative terminal completion of one connect challenge.
 */
export type ConnectFlowStatusResponse = {
	status: ConnectFlowStatusResponse.status;
};
export namespace ConnectFlowStatusResponse {
	export enum status {
		PENDING = 'pending',
		CONNECTED = 'connected',
	}
}
