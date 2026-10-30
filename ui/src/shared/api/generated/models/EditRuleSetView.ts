/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EditRuleView } from './EditRuleView';
/**
 * The shared rule set a binding points at.
 */
export type EditRuleSetView = {
    /**
     * The set's owner.
     */
    created_by?: (string | null);
    /**
     * A curated set is never changed; an edit copies it.
     */
    curated: boolean;
    id: string;
    name: string;
    rules: Array<EditRuleView>;
};

