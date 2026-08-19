import { useState } from 'react';
import { Code2 } from 'lucide-react';
import { PageHeader, PageShell } from '@/shared/ui';
import { OAuthClientsSection } from './OAuthClientsSection';

type SettingsSection = 'developer';

export function SettingsPage() {
	const [activeSection, setActiveSection] = useState<SettingsSection>('developer');

	return (
		<PageShell spacing="space-y-0">
			<PageHeader
				title="Settings"
				subtitle="Manage your organization's configuration and integrations."
			/>

			<div className="flex min-h-0 flex-1">
				{/* Sidebar */}
				<nav className="border-border w-56 shrink-0 border-r p-4">
					<div className="space-y-1">
						<button
							type="button"
							onClick={(): void => setActiveSection('developer')}
							className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm ${
								activeSection === 'developer'
									? 'bg-accent text-foreground'
									: 'text-muted-foreground hover:bg-accent/50'
							}`}
						>
							<Code2 className="h-4 w-4" />
							Developer Settings
						</button>
					</div>
				</nav>

				{/* Content */}
				<div className="min-h-0 flex-1 overflow-y-auto p-6">
					{activeSection === 'developer' && <OAuthClientsSection />}
				</div>
			</div>
		</PageShell>
	);
}
