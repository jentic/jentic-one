import { AlertTriangle } from 'lucide-react';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Textarea } from '@/components/ui/Textarea';
import type { SchemeType } from '@/lib/credentials/schemes';

/**
 * The per-scheme credential fields. One component, four visual modes:
 *
 *  - **basic** — Username + Password.
 *  - **bearer / apiKey / unknown** — Single secret field with a
 *    scheme-specific label ("Bearer Token", "API Key", "Credential
 *    Value"). When `compound === true` (the canonical Secret +
 *    Identity overlay pattern) we additionally render the Identity
 *    field above the secret with the overlay-derived labels.
 *  - **oauth2** — Renders nothing here — OAuth2 lives in
 *    `OAuthBrokerFields`. We keep this branch in the type for type
 *    exhaustiveness; the parent gates rendering.
 *
 * In edit mode the secret field is optional (omitting it preserves
 * the encrypted value already stored). In create mode it is required
 * and natively validated by the browser.
 *
 * No internal state: parent owns `value` / `identity`. The parent is
 * also responsible for clearing fields between API selections — see
 * `CredentialFormFields`.
 */
export interface AuthTypeFieldsProps {
	schemeType: SchemeType;
	isEdit: boolean;
	value: string;
	onValueChange: (next: string) => void;
	identity: string;
	onIdentityChange: (next: string) => void;
	compound: boolean;
	secretLabel: string;
	identityLabel: string;
}

export function AuthTypeFields({
	schemeType,
	isEdit,
	value,
	onValueChange,
	identity,
	onIdentityChange,
	compound,
	secretLabel,
	identityLabel,
}: AuthTypeFieldsProps) {
	if (schemeType === 'oauth2') return null;

	if (schemeType === 'basic') {
		return (
			<>
				<div>
					<Label
						htmlFor="cred-username"
						className="text-muted-foreground mb-1 block text-xs"
					>
						Username
					</Label>
					<Input
						id="cred-username"
						type="text"
						value={identity}
						onChange={(e) => onIdentityChange(e.target.value)}
						placeholder="Your username"
						className="bg-background"
					/>
				</div>
				<div>
					<Label
						htmlFor="cred-password"
						className="text-muted-foreground mb-1 block text-xs"
						required={!isEdit}
					>
						Password
					</Label>
					<Input
						id="cred-password"
						type="password"
						value={value}
						onChange={(e) => onValueChange(e.target.value)}
						required={!isEdit}
						placeholder={isEdit ? 'Leave blank to keep existing' : 'Your password'}
						className="bg-background"
					/>
				</div>
			</>
		);
	}

	const tokenLabel = compound
		? secretLabel
		: schemeType === 'bearer'
			? 'Bearer Token'
			: schemeType === 'apiKey'
				? 'API Key'
				: 'Credential Value';

	return (
		<>
			{compound && (
				<div>
					<Label
						htmlFor="cred-identity"
						className="text-muted-foreground mb-1 block text-xs"
						required
					>
						{identityLabel}
					</Label>
					<Input
						id="cred-identity"
						type="text"
						value={identity}
						onChange={(e) => onIdentityChange(e.target.value)}
						placeholder={`Your ${identityLabel.toLowerCase()}`}
						required
						className="bg-background"
					/>
				</div>
			)}
			<div>
				<Label
					htmlFor="cred-token"
					className="text-muted-foreground mb-1 block text-xs"
					required={!isEdit}
				>
					{tokenLabel}
					{isEdit && (
						<span className="text-muted-foreground/60">
							{' '}
							(leave blank to keep existing)
						</span>
					)}
				</Label>
				<Textarea
					id="cred-token"
					value={value}
					onChange={(e) => onValueChange(e.target.value)}
					rows={3}
					required={!isEdit}
					placeholder="Paste your token or API key…"
					resizable="none"
					className="bg-background font-mono"
				/>
				<p className="text-muted-foreground mt-1 text-xs">
					<AlertTriangle className="-mt-0.5 inline h-3 w-3" /> Stored encrypted. Never
					shown again after saving.
				</p>
			</div>
		</>
	);
}
