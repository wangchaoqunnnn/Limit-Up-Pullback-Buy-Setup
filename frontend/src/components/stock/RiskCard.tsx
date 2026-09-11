import type { RiskInfo } from '../../api/types';
import { riskTone } from '../../utils/constants';
import { Badge } from '../ui/Badge';

export interface RiskCardProps {
  risk: RiskInfo | null | undefined;
}

/** 风险提示卡：风险等级 + 风险点列表 */
export function RiskCard({ risk }: RiskCardProps) {
  const level = risk?.riskLevel ?? null;
  const points = Array.isArray(risk?.riskPoints) ? risk?.riskPoints.filter(Boolean) ?? [] : [];

  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row-between">
        <span className="text-2 fs-12">风险等级</span>
        <Badge tone={riskTone(level)} dot>
          {level ?? '未评估'}
        </Badge>
      </div>

      {points.length > 0 ? (
        <ul className="risk-points">
          {points.map((point, index) => (
            <li key={`${index}-${point.slice(0, 12)}`}>
              <span>{point}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="fs-12 text-3">未发现明显风险点，仍需严格执行止损纪律。</div>
      )}

      <div className="fs-11 text-3" style={{ lineHeight: 1.7 }}>
        风险点由量能、支撑、位置与板块四类规则机械生成，仅供研究参考。
      </div>
    </div>
  );
}
