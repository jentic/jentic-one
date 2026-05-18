import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { JenticLogo } from '@/components/ui/Logo';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { ErrorAlert } from '@/components/ui/ErrorAlert';
import { apiUrl } from '@/api/client';

export default function LoginPage() {
	const [username, setUsername] = useState('');
	const [password, setPassword] = useState('');
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const next = searchParams.get('next') || '/';

	const loginMutation = useMutation({
		mutationFn: async () => {
			const res = await fetch(apiUrl('/user/login'), {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				credentials: 'include',
				body: JSON.stringify({ username, password }),
			});
			if (!res.ok) throw new Error('Login failed');
			return res.json();
		},
		onSuccess: async () => {
			await queryClient.invalidateQueries({ queryKey: ['user', 'me'] });
			navigate(next, { replace: true });
		},
	});

	return (
		<div className="bg-background text-foreground flex min-h-screen items-center justify-center">
			<div className="bg-muted border-border w-full max-w-sm rounded-xl border p-8 shadow-2xl">
				<div className="mb-8 flex justify-center">
					{/* Preserve the 77:24 wordmark aspect ratio when scaling up. */}
					<JenticLogo width={128} height={40} />
				</div>

				<form
					onSubmit={(e) => {
						e.preventDefault();
						loginMutation.mutate();
					}}
				>
					{loginMutation.isError && (
						<div className="mb-4">
							<ErrorAlert message="Invalid username or password." />
						</div>
					)}

					<div className="mb-4">
						<Label
							htmlFor="login-username"
							className="text-muted-foreground mb-2 block font-bold"
						>
							Username
						</Label>
						<Input
							id="login-username"
							type="text"
							value={username}
							onChange={(e) => setUsername(e.target.value)}
							required
							className="bg-background"
						/>
					</div>

					<div className="mb-8">
						<Label
							htmlFor="login-password"
							className="text-muted-foreground mb-2 block font-bold"
						>
							Password
						</Label>
						<Input
							id="login-password"
							type="password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							required
							showPasswordToggle
							className="bg-background"
						/>
					</div>

					<Button type="submit" loading={loginMutation.isPending} size="lg" fullWidth>
						{loginMutation.isPending ? 'Logging in...' : 'Log In'}
					</Button>
				</form>

				<p className="text-muted-foreground mt-6 text-center text-xs">
					To reset your password, run{' '}
					<code className="bg-background px-1 font-mono">
						docker exec -it jentic-mini python3 -m src reset-password
					</code>
					.
				</p>
			</div>
		</div>
	);
}
