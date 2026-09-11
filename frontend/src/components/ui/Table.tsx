import clsx from 'clsx';
import { Fragment } from 'react';
import type { ReactNode } from 'react';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { SkeletonRows } from './Skeleton';

export type SortOrder = 'asc' | 'desc';

export interface Column<T> {
  key: string;
  title: ReactNode;
  width?: number | string;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  className?: string;
  /** 横向滚动时固定在容器一侧（操作列固定右侧可避免按钮被裁切） */
  sticky?: 'left' | 'right';
  render?: (row: T, index: number) => ReactNode;
}

export interface TableSort {
  field: string;
  order: SortOrder;
}

export interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onRowClick?: (row: T) => void;
  sort?: TableSort | null;
  onSortChange?: (next: TableSort) => void;
  minWidth?: number;
  emptyTitle?: string;
  emptyDesc?: string;
  emptyAction?: ReactNode;
  rowClassName?: (row: T, index: number) => string | undefined;
  skeletonRows?: number;
  ariaLabel?: string;
  /** 展开行渲染（配合 expandedKey） */
  expandedKey?: string | null;
  renderExpanded?: (row: T) => ReactNode;
  /** 表格底部（分页等） */
  footer?: ReactNode;
  className?: string;
}

export function Table<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  onRetry,
  onRowClick,
  sort = null,
  onSortChange,
  minWidth = 720,
  emptyTitle = '暂无数据',
  emptyDesc = '当前筛选条件下没有匹配记录，可调整筛选条件后重试。',
  emptyAction,
  rowClassName,
  skeletonRows = 8,
  ariaLabel,
  expandedKey = null,
  renderExpanded,
  footer,
  className,
}: TableProps<T>) {
  const colCount = columns.length;

  const handleSort = (col: Column<T>) => {
    if (!col.sortable || !onSortChange) return;
    const nextOrder: SortOrder = sort && sort.field === col.key && sort.order === 'desc' ? 'asc' : 'desc';
    onSortChange({ field: col.key, order: nextOrder });
  };

  return (
    <div className={clsx('table-wrap', className)}>
      <table className="table" style={{ minWidth }} aria-label={ariaLabel}>
        <colgroup>
          {columns.map((col) => (
            <col key={col.key} style={col.width === undefined ? undefined : { width: col.width }} />
          ))}
        </colgroup>
        <thead>
          <tr>
            {columns.map((col) => {
              const active = sort?.field === col.key;
              return (
                <th
                  key={col.key}
                  scope="col"
                  style={{ width: col.width, textAlign: col.align ?? 'left' }}
                  className={clsx(col.sortable && 'sortable', col.sticky && `col-sticky-${col.sticky}`)}
                  aria-sort={active ? (sort?.order === 'asc' ? 'ascending' : 'descending') : undefined}
                  tabIndex={col.sortable ? 0 : undefined}
                  role={col.sortable ? 'button' : undefined}
                  onClick={() => handleSort(col)}
                  onKeyDown={(e) => {
                    if (!col.sortable) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleSort(col);
                    }
                  }}
                >
                  {col.title}
                  {col.sortable && (
                    <span className="sort-ind" aria-hidden="true">
                      <span className={clsx(active && sort?.order === 'asc' && 'on')}>▲</span>
                      <span className={clsx(active && sort?.order === 'desc' && 'on')}>▼</span>
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 1 }).map((_, i) => (
              <tr key={`sk-${i}`}>
                <td colSpan={colCount} style={{ padding: 0, border: 0 }}>
                  <SkeletonRows rows={skeletonRows} cols={Math.min(colCount, 8)} />
                </td>
              </tr>
            ))}

          {!loading && error && (
            <tr>
              <td colSpan={colCount} style={{ padding: 0, border: 0 }}>
                <ErrorState message={error} onRetry={onRetry} />
              </td>
            </tr>
          )}

          {!loading && !error && rows.length === 0 && (
            <tr>
              <td colSpan={colCount} style={{ padding: 0, border: 0 }}>
                <EmptyState title={emptyTitle} desc={emptyDesc} action={emptyAction} />
              </td>
            </tr>
          )}

          {!loading &&
            !error &&
            rows.map((row, index) => {
              const key = rowKey(row, index);
              const clickable = Boolean(onRowClick);
              const expanded = expandedKey !== null && expandedKey === key && Boolean(renderExpanded);
              return (
                <Fragment key={key}>
                  <tr
                    className={clsx(clickable && 'clickable', expanded && 'expanded', rowClassName?.(row, index))}
                    tabIndex={clickable ? 0 : undefined}
                    onClick={clickable ? () => onRowClick?.(row) : undefined}
                    onKeyDown={
                      clickable
                        ? (e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              onRowClick?.(row);
                            }
                          }
                        : undefined
                    }
                  >
                    {columns.map((col) => (
                      <td
                        key={col.key}
                        className={clsx(
                          col.align === 'right' && 'cell-num',
                          col.sticky && `col-sticky-${col.sticky}`,
                          col.className,
                        )}
                        style={{ textAlign: col.align ?? 'left' }}
                      >
                        {col.render ? col.render(row, index) : null}
                      </td>
                    ))}
                  </tr>
                  {expanded && (
                    <tr>
                      <td colSpan={colCount} style={{ padding: 0, border: 0 }}>
                        {renderExpanded?.(row)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
        </tbody>
      </table>
      {footer}
    </div>
  );
}
