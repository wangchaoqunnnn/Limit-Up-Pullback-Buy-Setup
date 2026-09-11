import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { PoolItem } from '../api/types';
import { useApi } from '../hooks/useApi';
import { useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Stat } from '../components/ui/Stat';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Modal } from '../components/ui/Modal';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { Skeleton } from '../components/ui/Skeleton';
import { useToast } from '../components/ui/Toast';
import { LimitUpTypeTag } from '../components/stock/LimitUpTypeTag';
import { poolStatusMeta } from '../utils/constants';
import { fmtDate, fmtDateTimeShort, fmtPct, fmtPrice, isNum } from '../utils/format';

/** 自选低吸池：状态标签 + 盈亏着色 + 买入/止损/止盈位 + 删除 */
export default function Pool() {
  const toast = useToast();
  const pool = useApi((signal) => api.pool(signal), []);
  const [pendingDelete, setPendingDelete] = useState<PoolItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  // 开盘期间自动刷新低吸池盈亏（静默刷新：已有数据时不清空、不显示骨架屏）
  useAutoRefreshTarget(pool.refetchAsync);

  const items = pool.data?.items ?? [];
  const summary = pool.data?.summary;

  const confirmDelete = async () => {
    if (!pendingDelete?.meta?.code) return;
    setDeleting(true);
    try {
      await api.removePool(pendingDelete.meta.code);
      toast.success(`${pendingDelete.meta.name} 已从低吸池移除`);
      setPendingDelete(null);
      pool.refetch();
    } catch (err) {
      toast.error(`移除失败：${toErrorMessage(err)}`);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="自选低吸池"
        sub={
          pool.data
            ? `共 ${pool.data.total} 只跟踪标的 · 状态与盈亏按最新收盘价重算`
            : '跟踪中的低吸标的与预设买卖点'
        }
        actions={
          <Button loading={pool.refreshing} onClick={pool.refetch}>
            刷新盈亏
          </Button>
        }
      />

      {pool.error && pool.data && (
        <div className="mt-2">
          <InlineAlert message={pool.error} onRetry={pool.refetch} hint="以下盈亏为上一次成功获取的数据。" />
        </div>
      )}

      {/* 汇总条 */}
      <Card className="mt-4" title="池内汇总">
        {pool.loading && (
          <div className="stat-grid">
            {Array.from({ length: 5 }).map((_, i) => (
              <div className="stat" key={i}>
                <Skeleton width="50%" height={12} />
                <Skeleton width="70%" height={24} className="mt-2" />
              </div>
            ))}
          </div>
        )}
        {!pool.loading && pool.error && !pool.data && <ErrorState message={pool.error} compact onRetry={pool.refetch} />}
        {!pool.loading && summary && (
          <div className="stat-grid">
            <Stat label="跟踪标的" value={pool.data?.total ?? 0} unit="只" />
            <Stat
              label="平均盈亏"
              value={fmtPct(summary.avgPnlPct)}
              dir={summary.avgPnlPct > 0 ? 'up' : summary.avgPnlPct < 0 ? 'down' : 'flat'}
              foot="按最新收盘价"
            />
            <Stat label="已触发买入" value={summary.triggeredCount ?? 0} unit="只" />
            <Stat label="已止盈" value={summary.targetCount ?? 0} unit="只" />
            <Stat label="已止损" value={summary.stoppedCount ?? 0} unit="只" />
          </div>
        )}
      </Card>

      <div className="mt-4">
        {pool.loading && (
          <div className="pool-grid">
            {Array.from({ length: 3 }).map((_, i) => (
              <Card key={i}>
                <Skeleton width="40%" height={16} />
                <Skeleton width="60%" height={26} className="mt-2" />
                <Skeleton width="100%" height={54} className="mt-2" />
                <Skeleton width="100%" height={30} className="mt-2" />
              </Card>
            ))}
          </div>
        )}

        {!pool.loading && !pool.error && items.length === 0 && (
          <Card>
            <EmptyState
              title="低吸池为空"
              desc="在「信号选股」页点击「加入低吸池」，即可把命中战法的标的加入跟踪，并自动带入买入区间、止损与止盈位。"
              action={
                <Link className="btn btn-primary" to="/signals">
                  去信号选股
                </Link>
              }
            />
          </Card>
        )}

        {!pool.loading && items.length > 0 && (
          <div className="pool-grid">
            {items.map((item) => {
              const meta = poolStatusMeta(item.status, item.statusText);
              const pnl = isNum(item.pnlPct) ? item.pnlPct : 0;
              const pnlDir = pnl > 0 ? 'up' : pnl < 0 ? 'down' : 'flat';
              const price = isNum(item.lastClose) ? item.lastClose : null;
              const inBuyZone =
                price !== null && isNum(item.buyLow) && isNum(item.buyHigh)
                  ? price >= (item.buyLow as number) && price <= (item.buyHigh as number)
                  : false;
              const belowStop = price !== null && isNum(item.stopLoss) ? price <= (item.stopLoss as number) : false;
              const aboveTarget1 = price !== null && isNum(item.takeProfit1) ? price >= (item.takeProfit1 as number) : false;

              return (
                <Card key={item.id ?? item.meta?.code} className="pool-card" hover>
                  <div className="pool-head">
                    <div style={{ minWidth: 0 }}>
                      <div className="row" style={{ gap: 6 }}>
                        <Link to={`/stock/${item.meta?.code}`} className="strong" style={{ fontSize: 16 }}>
                          {item.meta?.name ?? '—'}
                        </Link>
                        <Badge tone={meta.tone} dot>
                          {item.statusText || meta.label}
                        </Badge>
                      </div>
                      <div className="stock-cell-meta" style={{ marginTop: 2 }}>
                        {item.meta?.code} · {item.meta?.industry} · 涨停 {fmtDate(item.limitUpDate)}
                      </div>
                      <div className="row" style={{ gap: 6, marginTop: 6 }}>
                        <LimitUpTypeTag type={item.limitUpType} />
                        {inBuyZone && <span className="tag tag-brand">处于买入区间</span>}
                        {belowStop && <span className="tag tag-teal">已破止损</span>}
                        {aboveTarget1 && <span className="tag tag-danger">已达止盈一</span>}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className={`pool-pnl ${pnlDir}`}>{fmtPct(item.pnlPct)}</div>
                      <div className="fs-11 text-3">
                        现价 {fmtPrice(price)} · {fmtDate(item.lastDate)}
                      </div>
                    </div>
                  </div>

                  <div className="pool-levels">
                    <div className="plan-item">
                      <div className="plan-k">买入区间</div>
                      <div className="plan-v brand-text" style={{ fontSize: 14 }}>
                        {fmtPrice(item.buyLow)} ~ {fmtPrice(item.buyHigh)}
                      </div>
                    </div>
                    <div className="plan-item">
                      <div className="plan-k">止损位</div>
                      <div className="plan-v" style={{ fontSize: 14 }}>
                        {fmtPrice(item.stopLoss)}
                      </div>
                    </div>
                    <div className="plan-item">
                      <div className="plan-k">止盈一 / 二</div>
                      <div className="plan-v" style={{ fontSize: 14 }}>
                        {fmtPrice(item.takeProfit1)} / {fmtPrice(item.takeProfit2)}
                      </div>
                    </div>
                    <div className="plan-item">
                      <div className="plan-k">加入价格</div>
                      <div className="plan-v" style={{ fontSize: 14 }}>
                        {fmtPrice(item.addedPrice)}
                      </div>
                    </div>
                  </div>

                  {item.note && <div className="pool-note">备注：{item.note}</div>}

                  <div className="table-foot" style={{ borderTop: '1px solid var(--border)' }}>
                    <span className="fs-11 text-3">加入于 {fmtDateTimeShort(item.addedAt)}</span>
                    <span className="row" style={{ gap: 6 }}>
                      <Link className="btn btn-sm" to={`/stock/${item.meta?.code}`}>
                        详情
                      </Link>
                      <Button size="sm" variant="danger" onClick={() => setPendingDelete(item)}>
                        移除
                      </Button>
                    </span>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <Modal
        open={pendingDelete !== null}
        title="移除确认"
        onClose={() => setPendingDelete(null)}
        footer={
          <>
            <Button onClick={() => setPendingDelete(null)}>取消</Button>
            <Button variant="danger" loading={deleting} onClick={confirmDelete}>
              确认移除
            </Button>
          </>
        }
      >
        确定要将「{pendingDelete?.meta?.name ?? ''}（{pendingDelete?.meta?.code ?? ''}）」从自选低吸池中移除吗？
        该操作仅删除跟踪记录，不会影响行情数据。
      </Modal>
    </>
  );
}
