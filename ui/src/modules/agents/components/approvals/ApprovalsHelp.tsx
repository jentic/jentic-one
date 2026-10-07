import { PageHelp } from '@/shared/ui';

/** Contextual help shared by the approvals list and detail pages. */
export function ApprovalsHelp() {
	return (
		<PageHelp
			title="About execution approvals"
			intro={
				<p>
					A permission rule with the <strong>Ask</strong> effect holds an agent&apos;s
					call instead of running it. The call waits here until a reviewer approves or
					denies it, or the approval expires.
				</p>
			}
			sections={[
				{
					heading: 'Who can review',
					body: (
						<p>
							The agent&apos;s owner or an org admin. An agent can never decide its
							own calls. Approvals for an agent without an owner are admin-only.
						</p>
					),
				},
				{
					heading: 'What happens next',
					body: (
						<p>
							Approving runs the call once, exactly as shown, with the credential
							injected at run time. Denying or letting it expire fails the call; the
							agent sees a permission-denied result and must ask again.
						</p>
					),
				},
			]}
		/>
	);
}
