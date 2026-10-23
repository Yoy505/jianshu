import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { Skeleton } from '@/components/ui/skeleton'
import { DataState } from './DataState'

/** 列表数据三态：加载骨架 / 错误重试 / 空态，数据就绪后渲染。 */
export function AsyncList<T>({ query, children }: {
  query: UseQueryResult<T>
  children: (data: T) => ReactNode
}) {
  if (query.isLoading) return <Skeleton className="h-48 w-full" />
  if (query.isError) {
    return <DataState title="加载失败" description="网络或服务异常，请重试" onRetry={() => query.refetch()} />
  }
  if (query.data == null) return <DataState title="暂无数据" />
  return <>{children(query.data)}</>
}
