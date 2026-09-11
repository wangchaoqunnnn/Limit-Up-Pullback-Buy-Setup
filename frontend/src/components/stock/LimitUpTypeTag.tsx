import type { LimitUpType } from '../../api/types';
import { LIMIT_UP_TYPE_META } from '../../utils/constants';
import { Tag } from '../ui/Tag';

export interface LimitUpTypeTagProps {
  type: LimitUpType | null | undefined;
  full?: boolean;
}

/** 涨停质量分类标签（中文全称 / 简称 + 判定说明 tooltip） */
export function LimitUpTypeTag({ type, full = false }: LimitUpTypeTagProps) {
  if (!type) return <span className="text-3">—</span>;
  const meta = LIMIT_UP_TYPE_META[type];
  if (!meta) return <Tag>{type}</Tag>;
  return (
    <Tag tone={meta.tone} title={`${meta.label}：${meta.desc}`}>
      {full ? meta.label : meta.short}
    </Tag>
  );
}
