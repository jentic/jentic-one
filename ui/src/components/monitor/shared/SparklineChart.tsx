import { type JSX, useMemo } from 'react';
import { cn } from '@/lib/utils';

interface SparklineChartProps {
	data: number[];
	width?: number;
	height?: number;
	strokeWidth?: number;
	className?: string;
	color?: string;
}

export function SparklineChart({
	data,
	width = 80,
	height = 24,
	strokeWidth = 1.5,
	className,
	color,
}: SparklineChartProps): JSX.Element {
	const pathD = useMemo(() => {
		if (data.length < 2) return '';

		const min = Math.min(...data);
		const max = Math.max(...data);
		const range = max - min || 1;

		const padding = 2;
		const chartWidth = width - padding * 2;
		const chartHeight = height - padding * 2;

		const points = data.map((value, index) => {
			const x = padding + (index / (data.length - 1)) * chartWidth;
			const y = padding + chartHeight - ((value - min) / range) * chartHeight;
			return `${x},${y}`;
		});

		return `M ${points.join(' L ')}`;
	}, [data, width, height]);

	if (data.length < 2) {
		return <div className={cn('h-6 w-20', className)} />;
	}

	return (
		<svg
			width={width}
			height={height}
			viewBox={`0 0 ${width} ${height}`}
			className={cn('overflow-visible', className)}
		>
			<path
				d={pathD}
				fill="none"
				stroke={color ?? 'currentColor'}
				strokeWidth={strokeWidth}
				strokeLinecap="round"
				strokeLinejoin="round"
				className={cn(!color && 'text-muted-foreground')}
			/>
		</svg>
	);
}
