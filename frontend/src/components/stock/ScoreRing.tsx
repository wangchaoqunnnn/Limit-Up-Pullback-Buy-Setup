import { useId } from 'react';
import clsx from 'clsx';
import { clamp, fmtScore } from '../../utils/format';

export interface ScoreRingProps {
  score: number | null | undefined;
  size?: number;
  thickness?: number;
  /** 是否显示“分”字样与满分刻度 */
  showCap?: boolean;
  className?: string;
  ariaLabel?: string;
}

/** 评分环形进度（渐变描边 + 内圈柔光） */
export function ScoreRing({
  score,
  size = 84,
  thickness = 7,
  showCap = true,
  className,
  ariaLabel,
}: ScoreRingProps) {
  const gradientId = useId().replace(/[:]/g, '');
  const value = typeof score === 'number' && Number.isFinite(score) ? clamp(score, 0, 100) : null;
  const pct = value === null ? 0 : value / 100;
  const r = size / 2 - thickness / 2 - 1;
  const circumference = 2 * Math.PI * r;
  const dash = circumference * pct;

  const tone = value === null ? 'var(--text-3)' : value >= 85 ? 'var(--gold)' : value >= 75 ? 'var(--teal)' : value >= 55 ? 'var(--brand)' : 'var(--text-3)';

  // 字号按字符数自适应：等宽字体单字宽约 0.62em，"100.0" 这类 5 字符也必须完整落在环内
  const text = fmtScore(value);
  const innerWidth = Math.max(20, 2 * r - 12);
  const fitFontSize = innerWidth / (Math.max(1, text.length) * 0.62);
  const fontSize = Math.max(11, Math.min(size * 0.3, fitFontSize));

  return (
    <div
      className={clsx('score-ring', className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={ariaLabel ?? `综合评分 ${value === null ? '暂无' : value.toFixed(1)} 分`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <linearGradient id={`ring-${gradientId}`} x1="0" y1="1" x2="1" y2="0">
            <stop offset="0%" style={{ stopColor: tone, stopOpacity: 0.55 }} />
            <stop offset="100%" style={{ stopColor: tone, stopOpacity: 1 }} />
          </linearGradient>
          <filter id={`ring-glow-${gradientId}`} x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="2.4" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          style={{ stroke: 'var(--bg-3)' }}
          strokeWidth={thickness}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#ring-${gradientId})`}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          filter={`url(#ring-glow-${gradientId})`}
          style={{ transition: 'stroke-dasharray 250ms cubic-bezier(0.4,0,0.2,1)' }}
        />
      </svg>
      <div className="score-ring-label">
        <span className="score-ring-value" style={{ fontSize, lineHeight: 1.05, color: tone }}>
          {text}
        </span>
        {showCap && <span className="score-ring-cap">综合评分</span>}
      </div>
    </div>
  );
}
