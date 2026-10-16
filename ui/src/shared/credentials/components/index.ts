/**
 * Shared credentials kit — component-tier barrel.
 *
 * The curated public surface of `shared/credentials/components`: the dialogs,
 * sheets, badges and connect hooks that feature modules (and downstream hosts
 * built on the module seams) compose. Consumers import from
 * `@/shared/credentials/components`, never a component file, so file moves
 * inside this folder don't break them. Anything not listed here is internal
 * to the kit.
 *
 * Files inside this folder import each other directly, never through this
 * barrel (that would create an import cycle).
 */

// Create / edit / delete
export { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
export type { CreatedCredentialInfo } from '@/shared/credentials/components/CreateCredentialFlow';
export { EditCredentialSheet } from '@/shared/credentials/components/EditCredentialSheet';
export { CredentialDeleteDialog } from '@/shared/credentials/components/CredentialDeleteDialog';
export type { CredentialDeleteDialogProps } from '@/shared/credentials/components/CredentialDeleteDialog';
export { ImportSpecDialog } from '@/shared/credentials/components/ImportSpecDialog';
export type { ImportSpecDialogProps } from '@/shared/credentials/components/ImportSpecDialog';
export { PostConnectBindMore } from '@/shared/credentials/components/PostConnectBindMore';

// Display
export { CredentialTypeBadge } from '@/shared/credentials/components/CredentialTypeBadge';
export {
	SharedWithYouBadge,
	credentialIsConnected,
	credentialIsPendingSignIn,
} from '@/shared/credentials/components/CredentialCard';
export { CredentialsList } from '@/shared/credentials/components/CredentialsList';
export { CredentialOptions } from '@/shared/credentials/components/CredentialOptions';
export type { CredentialOptionsProps } from '@/shared/credentials/components/CredentialOptions';

// API picking + access rules
export { ApiPicker } from '@/shared/credentials/components/ApiPicker';
export type { ApiPickerProps } from '@/shared/credentials/components/ApiPicker';
export { AccessRulesStep } from '@/shared/credentials/components/AccessRulesStep';
export type { AccessRulesStepProps } from '@/shared/credentials/components/AccessRulesStep';
export { RuleListEditor } from '@/shared/credentials/components/RuleListEditor';
export {
	MatchedRuleText,
	RuleRequestTester,
	VerdictChip,
	verdictOutcome,
} from '@/shared/credentials/components/RuleRequestTester';
export type { RuleRequestTesterProps } from '@/shared/credentials/components/RuleRequestTester';
export {
	OperationImpactPreview,
	useAllowedOperationCount,
} from '@/shared/credentials/components/OperationImpactPreview';
export type { OpsApiReference } from '@/shared/credentials/components/OperationImpactPreview';

// Connect flow
export { useConnectAfterCreate } from '@/shared/credentials/components/useConnectAfterCreate';
export { useDeviceAwareConnect } from '@/shared/credentials/components/useDeviceAwareConnect';
export type { ConnectedCredentialInfo } from '@/shared/credentials/components/VendorConnectFlow';
