import { useState, useCallback } from 'react';
import { BookOpen, ExternalLink, LogOut } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AppLink } from '@/components/ui/AppLink';
import { Button } from '@/components/ui/Button';
import { MenuPanel, MenuSeparator, menuItemClass, useDismissable } from '@/components/ui/Menu';
import { cn } from '@/lib/utils';
import { apiUrl } from '@/api/client';
import { useAuth } from '@/hooks/useAuth';
import { useUpdateCheck } from '@/hooks/useUpdateCheck';
import { UserService } from '@/api/generated';

export function UserMenu() {
	const { user } = useAuth();
	const { updateAvailable, latestVersion, releaseUrl, currentVersion } = useUpdateCheck();
	const [open, setOpen] = useState(false);
	const close = useCallback(() => setOpen(false), []);
	const menuRef = useDismissable<HTMLDivElement>(open, close);
	const queryClient = useQueryClient();
	const navigate = useNavigate();

	const logoutMutation = useMutation({
		mutationFn: () => UserService.logoutUserLogoutPost(),
		onSuccess: () => {
			queryClient.clear();
			navigate('/login');
		},
	});

	const initial = (user?.username ?? 'U')[0].toUpperCase();

	return (
		<div ref={menuRef} className="relative">
			<Button
				variant="ghost"
				onClick={() => setOpen((o) => !o)}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label="User menu"
				className="bg-primary/80 text-primary-foreground hover:bg-primary/80 hover:text-primary-foreground relative h-7 w-7 rounded-full p-0 text-xs font-semibold hover:opacity-80"
			>
				{initial}
				{updateAvailable && (
					<span className="border-background bg-accent-yellow absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2" />
				)}
			</Button>

			{open && (
				<MenuPanel align="right" className="mt-2 w-60">
					{/* Identity header: username + version subtitle */}
					<div className="px-2.5 pt-2 pb-2">
						<div className="text-foreground truncate text-sm font-semibold">
							{user?.username ?? 'User'}
						</div>
						{currentVersion && (
							<div className="text-muted-foreground/70 mt-0.5 font-mono text-[10px]">
								v{currentVersion}
							</div>
						)}
					</div>

					<MenuSeparator />

					{/* Update available — promoted to top of the action group */}
					{updateAvailable && releaseUrl && (
						<AppLink
							href={releaseUrl}
							role="menuitem"
							onClick={close}
							className={cn(menuItemClass(), 'text-accent-yellow')}
						>
							<span className="bg-accent-yellow h-1.5 w-1.5 shrink-0 animate-pulse rounded-full" />
							<span className="truncate">Update available: {latestVersion}</span>
						</AppLink>
					)}

					{/* External links */}
					<AppLink
						href={apiUrl('/docs')}
						external
						role="menuitem"
						onClick={close}
						className={menuItemClass()}
						aria-label="API docs (opens in a new tab)"
						title="API docs (opens in a new tab)"
					>
						<BookOpen className="h-4 w-4 shrink-0" />
						API docs
						<ExternalLink
							className="ml-auto h-3 w-3 shrink-0 opacity-60"
							aria-hidden="true"
						/>
					</AppLink>

					<AppLink
						href="https://jentic.com"
						role="menuitem"
						onClick={close}
						className={menuItemClass()}
						aria-label="More at jentic.com (opens in a new tab)"
						title="More at jentic.com (opens in a new tab)"
					>
						<ExternalLink className="h-4 w-4 shrink-0" aria-hidden="true" />
						More at jentic.com
					</AppLink>

					<MenuSeparator />

					{/* Destructive action */}
					<Button
						variant="ghost"
						role="menuitem"
						onClick={() => {
							close();
							logoutMutation.mutate();
						}}
						className={cn(
							menuItemClass(),
							'justify-start',
							logoutMutation.isPending && 'opacity-60',
						)}
						disabled={logoutMutation.isPending}
					>
						<LogOut className="h-4 w-4 shrink-0" />
						{logoutMutation.isPending ? 'Logging out…' : 'Log out'}
					</Button>
				</MenuPanel>
			)}
		</div>
	);
}
