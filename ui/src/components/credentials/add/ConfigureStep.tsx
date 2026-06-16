import { CredentialFormFields } from '@/components/credentials/form/CredentialFormFields';
import type { ApiOut } from '@/api/types';

/**
 * Step 3 of `<AddCredentialDialog>` — drops the user into the same
 * `<CredentialFormFields>` body the legacy `/credentials/new` page
 * uses, with the API summary chip hidden (the dialog's title bar
 * already names the API + we render our own summary in the surround).
 *
 * Save success bubbles back to the parent through `onSaved` so the
 * dialog can run the toolkit-bind side effect (toolkit mode) and
 * advance to the Confirm step.
 */
export interface ConfigureStepProps {
	selectedApi: ApiOut;
	onBack: () => void;
	onSaved: (saved: { id: string; api_id: string }) => void;
}

export function ConfigureStep({ selectedApi, onBack, onSaved }: ConfigureStepProps) {
	return (
		<CredentialFormFields
			selectedApi={selectedApi}
			onBack={onBack}
			onSaved={onSaved}
			hideApiSummary
			layout="dialog"
		/>
	);
}
