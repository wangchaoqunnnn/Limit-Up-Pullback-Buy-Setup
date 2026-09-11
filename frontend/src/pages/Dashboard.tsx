import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { useApi } from '../hooks/useApi';
import { useAutoRefreshTarget } from '../hooks/useAutoRefresh';
import { useScanTask } from '../hooks/useScanTask';
import { PageHeader } from '../components/layout/PageHeader';
import { Card } from '../components/ui/Card';
import { Stat } from '../components/ui/Stat';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Tooltip } from '../components/ui/Tooltip';
import { EmptyState } from '../components/ui/EmptyState';
import { ErrorState, InlineAlert } from '../components/ui/ErrorState';
import { ProgressBar, ProgressLine } from '../components/ui/ProgressBar';
import { useToast } from '../components/ui/Toast';
import { Skeleton, SkeletonChart, SkeletonRows } from '../components/ui/Skeleton';
import { Sparkline } from '../components/charts/Sparkline';
import { GaugeChart } from '../components/charts/GaugeChart';
import { DonutChart } from '../components/charts/DonutChart';
import { BarChart } from '../components/charts/BarChart';
import { VerdictBadge } from '../components/stock/VerdictBadge';
import { SIGNAL_KEYS, SIGNAL_META, dataSourceLabel, sentimentTone } from '../utils/constants';
import { clamp, fmtAmount, fmtDateTime, fmtInt, fmtPct, fmtPrice, fmtScore, isNum } from '../utils/format';

/** 市场总览 Dashboard：指数卡片 + 情绪仪表盘 + 五信号通过率 + 评分分布 + TOP 低吸榜 + 板块热度 */
export default function Dashboard() {
  const navigate = useNavigate();
  const toast = useToast();

  const overview = useApi((signal) => api.marketOverview(false, signal), [], { pollMs: 120000 });
  const top = useApi(
    (signal) =>
      api.signals({ verdict: 'BUY,WATCH', sort: 'score', order: 'desc', page: 1, pageSize: 5 }, signal),
    [],
  );
  const scan = useScanTask(() => {
    overview.refetch();
    top.refetch();
    toast.success('全市场扫描完成，选股结果已刷新');
  });

  // 开盘期间自动刷新主数据（静默刷新：保留旧数据，不显示骨架屏）
  useAutoRefreshTarget(overview.refetchAsync, top.refetchAsync);

  const sentiment = overview.data?.sentiment;
  const passRate = top.data?.statistics?.signalPassRate ?? {};
  const breadthTotal = (sentiment?.upCount ?? 0) + (sentiment?.downCount ?? 0) + (sentiment?.flatCount ?? 0);

  const passRateData = SIGNAL_KEYS.map((key) => {
    const rate = passRate[key];
    return {
      label: SIGNAL_META[key].short,
      value: isNum(rate) ? rate * 100 : 0,
      hint: isNum(rate) ? `${(rate * 100).toFixed(1)}% 通过率` : '暂无数据',
    };
  });

  const distribution = overview.data?.scoreDistribution;

  return (
    <>
      <PageHeader
        title="市场总览"
        sub={
          overview.data
            ? `交易日 ${overview.data.tradeDate} · 更新于 ${fmtDateTime(overview.data.updatedAt)} · 数据源 ${dataSourceLabel(overview.data.dataSource)}`
            : '指数、情绪与信号通过率的实时终端视图'
        }
        actions={
          <>
            <Button
              variant="primary"
              loading={scan.running}
              onClick={() => {
                void scan.start({ refresh: true, limitUpType: 'QUALITY' });
              }}
              title="重新回放全市场历史 K 线并刷新选股结果"
              aria-label="重新扫描全市场"
            >
              重新扫描全市场
            </Button>
            <Button
              loading={overview.refreshing}
              onClick={() => {
                overview.refetch();
                top.refetch();
              }}
              title="仅重新拉取后端已缓存的数据，不触发全市场扫描"
            >
              刷新
            </Button>
          </>
        }
      />

      {overview.error && overview.data && (
        <div className="mt-2">
          <InlineAlert
            message={overview.error}
            onRetry={overview.refetch}
            hint="市场总览每 2 分钟自动刷新一次。"
          />
        </div>
      )}

      {(scan.task || scan.error) && (
        <Card
          className="mt-2"
          title="扫描任务"
          sub={scan.task?.message ?? '正在触发全市场扫描…'}
          extra={
            scan.task ? (
              <Badge
                tone={
                  scan.task.status === 'finished' ? 'teal' : scan.task.status === 'failed' ? 'danger' : 'brand'
                }
                dot
              >
                {scan.task.status === 'finished'
                  ? '已完成'
                  : scan.task.status === 'failed'
                    ? '失败'
                    : scan.task.status === 'pending'
                      ? '排队中'
                      : '扫描中'}
              </Badge>
            ) : undefined
          }
        >
          {scan.error ? (
            <ErrorState
              message={scan.error}
              compact
              onRetry={() => {
                void scan.start({ refresh: true });
              }}
            />
          ) : (
            <div className="col" style={{ gap: 8 }}>
              <ProgressBar
                value={scan.progressPct / 100}
                showValue
                size="lg"
                label={`进度 ${scan.task?.progress ?? 0} / ${scan.task?.total ?? 0}`}
                ariaLabel="扫描进度"
              />
              <div className="row fs-12 text-2" style={{ justifyContent: 'space-between' }}>
                <span>
                  {scan.task?.matched !== null && scan.task?.matched !== undefined
                    ? `命中 ${scan.task.matched} 只标的`
                    : '正在逐只回放历史 K 线…'}
                </span>
                <button type="button" className="btn btn-sm btn-ghost" onClick={scan.reset}>
                  收起
                </button>
              </div>
            </div>
          )}
        </Card>
      )}

      {overview.error && !overview.data && (
        <Card className="mt-4">
          <ErrorState message={overview.error} onRetry={overview.refetch} />
        </Card>
      )}

      {/* 指数卡片 */}
      <div className="index-cards mt-4">
        {overview.loading &&
          Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="index-card">
              <Skeleton width="48%" height={12} />
              <Skeleton width="70%" height={26} className="mt-2" />
              <Skeleton width="100%" height={30} className="mt-2" />
            </Card>
          ))}

        {(overview.data?.indexes ?? []).map((index) => {
          const dir = isNum(index.pctChg) && index.pctChg > 0 ? 'up' : isNum(index.pctChg) && index.pctChg < 0 ? 'down' : 'flat';
          return (
            <Card key={index.code} className="index-card" hover>
              <div className="index-name">
                <span>{index.name}</span>
                <span className="mono fs-11 text-3">{index.code}</span>
              </div>
              <div className="row-between" style={{ marginTop: 4 }}>
                <span className={`index-close ${dir}`}>{fmtPrice(index.close)}</span>
                <Badge tone={dir === 'up' ? 'up' : dir === 'down' ? 'down' : 'flat'}>
                  {fmtPct(index.pctChg)}
                </Badge>
              </div>
              <div style={{ marginTop: 6 }}>
                <Sparkline data={index.sparkline ?? []} height={34} tone={dir} baseline />
              </div>
            </Card>
          );
        })}

        {!overview.loading && (overview.data?.indexes ?? []).length === 0 && !overview.error && (
          <Card>
            <EmptyState title="暂无指数数据" desc="后端未返回指数行情，请稍后重试。" />
          </Card>
        )}
      </div>

      <div className="dash-grid mt-4">
        <div className="dash-left">
          {/* 市场情绪 */}
          <Card
            title="市场情绪仪表盘"
            sub="综合涨停家数、炸板率、涨跌家数与成交量能计算"
            extra={
              sentiment ? (
                <Badge tone={sentimentTone(sentiment.level)} dot>
                  {sentiment.level}
                </Badge>
              ) : undefined
            }
          >
            {overview.loading && <SkeletonChart height={280} />}
            {!overview.loading && sentiment && (
              <div className="col" style={{ gap: 16 }}>
                <div className="grid-2" style={{ gridTemplateColumns: 'minmax(220px, 300px) 1fr' }}>
                  <GaugeChart score={sentiment.score} level={sentiment.level} size={250} />
                  <div className="col" style={{ gap: 14, justifyContent: 'center' }}>
                    <div className="gauge-meta">
                      <div className="gauge-meta-item">
                        <div className={`gauge-meta-v ${sentiment.limitUpCount > 0 ? 'up' : 'flat'}`}>
                          {fmtInt(sentiment.limitUpCount)}
                        </div>
                        <div className="gauge-meta-k">涨停家数</div>
                      </div>
                      <div className="gauge-meta-item">
                        <div className={`gauge-meta-v ${sentiment.limitDownCount > 0 ? 'down' : 'flat'}`}>
                          {fmtInt(sentiment.limitDownCount)}
                        </div>
                        <div className="gauge-meta-k">跌停家数</div>
                      </div>
                      <div className="gauge-meta-item">
                        <div className={`gauge-meta-v ${sentiment.brokenBoardCount > 0 ? 'gold-text' : 'flat'}`}>
                          {fmtInt(sentiment.brokenBoardCount)}
                        </div>
                        <div className="gauge-meta-k">炸板家数</div>
                      </div>
                    </div>

                    <div className="breadth">
                      <div className="row-between fs-12 text-2">
                        <span>涨跌家数分布</span>
                        <span className="mono">
                          炸板率 {isNum(sentiment.brokenRate) ? `${(sentiment.brokenRate * 100).toFixed(1)}%` : '—'}
                        </span>
                      </div>
                      <div className="breadth-bar" role="img" aria-label="上涨、下跌、平盘家数占比">
                        {breadthTotal > 0 ? (
                          <>
                            <span
                              className="breadth-seg up"
                              style={{
                                flexBasis: `${(sentiment.upCount / breadthTotal) * 100}%`,
                                minWidth: sentiment.upCount > 0 ? 6 : 0,
                              }}
                              title={`上涨 ${sentiment.upCount} 家`}
                            >
                              {sentiment.upCount > 0 ? sentiment.upCount : ''}
                            </span>
                            <span
                              className="breadth-seg flat"
                              style={{
                                flexBasis: `${(sentiment.flatCount / breadthTotal) * 100}%`,
                                minWidth: sentiment.flatCount > 0 ? 6 : 0,
                              }}
                              title={`平盘 ${sentiment.flatCount} 家`}
                            >
                              {sentiment.flatCount > 0 ? sentiment.flatCount : ''}
                            </span>
                            <span
                              className="breadth-seg down"
                              style={{
                                flexBasis: `${(sentiment.downCount / breadthTotal) * 100}%`,
                                minWidth: sentiment.downCount > 0 ? 6 : 0,
                              }}
                              title={`下跌 ${sentiment.downCount} 家`}
                            >
                              {sentiment.downCount > 0 ? sentiment.downCount : ''}
                            </span>
                          </>
                        ) : (
                          <span className="breadth-seg flat" style={{ flexBasis: '100%' }}>
                            暂无数据
                          </span>
                        )}
                      </div>
                      <div className="breadth-legend">
                        <span className="breadth-legend-item">
                          <i className="swatch up" aria-hidden="true" />
                          上涨 {fmtInt(sentiment.upCount)}
                        </span>
                        <span className="breadth-legend-item">
                          <i className="swatch flat" aria-hidden="true" />
                          平盘 {fmtInt(sentiment.flatCount)}
                        </span>
                        <span className="breadth-legend-item">
                          <i className="swatch down" aria-hidden="true" />
                          下跌 {fmtInt(sentiment.downCount)}
                        </span>
                      </div>
                    </div>

                    <div className="stat-grid">
                      <Stat
                        size="sm"
                        label="全市场平均涨幅"
                        value={fmtPct(sentiment.avgPctChg)}
                        dir={sentiment.avgPctChg > 0 ? 'up' : sentiment.avgPctChg < 0 ? 'down' : 'flat'}
                      />
                      <Stat size="sm" label="两市总成交额" value={fmtAmount(sentiment.totalAmount)} />
                    </div>
                  </div>
                </div>
              </div>
            )}
            {!overview.loading && !sentiment && !overview.error && (
              <EmptyState title="暂无情绪数据" desc="后端未返回市场情绪字段。" />
            )}
          </Card>

          {/* 五信号通过率 + 评分分布 */}
          <div className="grid-2">
            <Card
              title="五大信号通过率"
              sub="当前命中池中每个信号的通过比例"
              extra={
                top.data ? (
                  <span className="mono fs-12">样本 {fmtInt(top.data.matched)} 只</span>
                ) : undefined
              }
            >
              {top.loading && <SkeletonChart height={190} />}
              {!top.loading && top.error && !top.data && <ErrorState message={top.error} compact onRetry={top.refetch} />}
              {!top.loading && top.data && (
                <BarChart
                  data={passRateData}
                  height={190}
                  maxValue={100}
                  valueFormat={(v) => `${v.toFixed(0)}%`}
                  ariaLabel="五大信号通过率柱状图"
                />
              )}
            </Card>

            <Card title="评分分布" sub="全市场标的评分结论分布">
              {overview.loading && <SkeletonChart height={230} />}
              {!overview.loading && distribution && (
                <DonutChart
                  data={[
                    { label: '可低吸', value: distribution.buy, color: 'var(--brand)' },
                    { label: '观察', value: distribution.watch, color: 'var(--gold)' },
                    { label: '放弃', value: distribution.reject, color: 'var(--arc-muted)' },
                  ]}
                  size={168}
                  centerLabel="全市场样本"
                  ariaLabel="评分结论分布环形图"
                />
              )}
              {!overview.loading && !distribution && !overview.error && (
                <EmptyState title="暂无评分分布" desc="后端未返回评分分布字段。" />
              )}
            </Card>
          </div>
        </div>

        <div className="dash-right">
          {/* 今日可低吸 TOP */}
          <Card
            title="今日可低吸 TOP"
            sub="按综合评分降序，仅列可低吸与观察标的"
            extra={
              <Button size="sm" variant="ghost" onClick={() => navigate('/signals')}>
                查看全部
              </Button>
            }
            flush
          >
            {top.loading && <SkeletonRows rows={5} cols={3} />}
            {!top.loading && top.error && !top.data && <ErrorState message={top.error} compact onRetry={top.refetch} />}
            {!top.loading && top.data && (top.data.items ?? []).length === 0 && (
              <EmptyState
                title="今日暂无低吸标的"
                desc="当前没有同时满足硬性门槛与五大共振信号的标的，建议保持空仓等待。"
              />
            )}
            {!top.loading && (top.data?.items ?? []).length > 0 && (
              <ul className="top-list">
                <li className="top-head" aria-hidden="true">
                  <span>#</span>
                  <span>标的</span>
                  <span className="row" style={{ gap: 14, justifyContent: 'flex-end' }}>
                    <span className="num" style={{ minWidth: 52 }}>
                      综合评分
                    </span>
                    <span className="num" style={{ minWidth: 60 }}>
                      回调幅度
                    </span>
                  </span>
                </li>
                {(top.data?.items ?? []).map((item, index) => (
                  <li
                    key={item.meta?.code ?? index}
                    className="top-item"
                    tabIndex={0}
                    role="button"
                    onClick={() => navigate(`/stock/${item.meta?.code}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        navigate(`/stock/${item.meta?.code}`);
                      }
                    }}
                  >
                    <span className={`top-rank r${index + 1}`}>{index + 1}</span>
                    <span className="stock-cell">
                      <span className="stock-cell-name">
                        {item.meta?.name}
                        <VerdictBadge verdict={item.verdict} size="sm" />
                      </span>
                      <span className="stock-cell-meta">
                        {item.meta?.code} · {item.meta?.industry} · 回调 {item.pullbackDays} 天
                      </span>
                    </span>
                    <span className="row" style={{ gap: 14, justifyContent: 'flex-end' }}>
                      <span className="mono strong" style={{ fontSize: 16, minWidth: 52, textAlign: 'right' }}>
                        {fmtScore(item.score)}
                      </span>
                      <span className="mono fs-12 text-2" style={{ minWidth: 60, textAlign: 'right' }}>
                        {fmtPct(item.pullbackPct)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* 板块热度榜 */}
          <Card
            title="板块热度榜"
            sub="按板块情绪评分降序"
            extra={
              top.data ? (
                <Tooltip content="板块情绪由板块涨幅、涨停家数与个股联动强度加权得出" align="right">
                  <span className="fs-11 text-3">情绪评分说明</span>
                </Tooltip>
              ) : undefined
            }
            flush
          >
            {overview.loading && <SkeletonRows rows={6} cols={4} />}
            {!overview.loading && (overview.data?.topIndustries ?? []).length === 0 && !overview.error && (
              <EmptyState title="暂无板块数据" desc="后端未返回板块热度榜。" />
            )}
            {(overview.data?.topIndustries ?? []).length > 0 && (
              <div className="industry-head" aria-hidden="true">
                <span>板块</span>
                <span>情绪强度</span>
                <span className="num">涨跌幅</span>
                <span className="num">涨停</span>
              </div>
            )}
            {(overview.data?.topIndustries ?? []).map((industry) => {
              const score = isNum(industry.sentimentScore) ? clamp(industry.sentimentScore, 0, 100) : 0;
              return (
                <div className="industry-row" key={industry.name}>
                  <span className="industry-name" title={industry.name}>
                    {industry.name}
                  </span>
                  <span className="heat" title={`情绪评分 ${score.toFixed(1)}`}>
                    <span className="heat-fill" style={{ width: `${Math.max(4, score)}%` }} />
                  </span>
                  <span
                    className={`mono fs-12 ${industry.pctChg > 0 ? 'up' : industry.pctChg < 0 ? 'down' : 'flat'}`}
                  >
                    {fmtPct(industry.pctChg)}
                  </span>
                  <span className="mono fs-12 text-2" style={{ textAlign: 'right' }}>
                    {fmtInt(industry.limitUpCount)}
                  </span>
                </div>
              );
            })}
          </Card>

          {/* 通过率明细 */}
          {top.data && (
            <Card title="信号强度明细" sub="通过率越高说明该信号在当前市场越有效">
              <div className="col" style={{ gap: 10 }}>
                {SIGNAL_KEYS.map((key) => {
                  const rate = passRate[key];
                  const value = isNum(rate) ? rate : 0;
                  return (
                    <ProgressLine
                      key={key}
                      value={value}
                      tone={value >= 0.6 ? 'teal' : value >= 0.35 ? 'gold' : 'flat'}
                      label={SIGNAL_META[key].label}
                      percentText={`${(value * 100).toFixed(1)}%`}
                    />
                  );
                })}
              </div>
            </Card>
          )}
        </div>
      </div>

      {overview.data && (
        <div className="fs-11 text-3 mt-4">
          数据由后端按固定规则机械计算，仅供技术研究参考；接口异常时请检查服务状态。
          {overview.updatedAt ? ` 最近一次成功更新：${new Date(overview.updatedAt).toLocaleTimeString('zh-CN')}` : ''}
        </div>
      )}
    </>
  );
}
