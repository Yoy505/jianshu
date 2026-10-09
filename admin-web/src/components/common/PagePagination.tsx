import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface PagePaginationProps {
  page: number
  pageSize: number
  total: number
  onChange: (page: number) => void
  className?: string
}

function buildPages(current: number, totalPages: number): Array<number | 'ellipsis'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1)
  }
  const pages: Array<number | 'ellipsis'> = [1]
  const from = Math.max(2, current - 1)
  const to = Math.min(totalPages - 1, current + 1)
  if (from > 2) pages.push('ellipsis')
  for (let i = from; i <= to; i++) pages.push(i)
  if (to < totalPages - 1) pages.push('ellipsis')
  if (totalPages > 1) pages.push(totalPages)
  return pages
}

export function PagePagination({ page, pageSize, total, onChange, className }: PagePaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const current = Math.min(Math.max(1, page), totalPages)
  const pages = buildPages(current, totalPages)

  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-5 py-4', className)}>
      <p className="text-sm text-muted-foreground">
        共 <span className="font-medium tabular-nums text-foreground">{total}</span> 条
      </p>
      {totalPages > 1 ? (
        <div className="flex max-w-full items-center gap-1 overflow-x-auto" aria-label="分页导航">
          <Button
            variant="outline"
            size="icon"
            className="size-9 shrink-0"
            disabled={current <= 1}
            onClick={() => onChange(current - 1)}
          >
            <ChevronLeft />
            <span className="sr-only">上一页</span>
          </Button>
          {pages.map((item, index) =>
            item === 'ellipsis' ? (
              <span key={`ellipsis-${index}`} className="px-1 text-xs text-muted-foreground">
                …
              </span>
            ) : (
              <Button
                key={item}
                variant={item === current ? 'default' : 'outline'}
                size="icon"
                className="size-9 shrink-0 text-xs tabular-nums"
                aria-label={`第 ${item} 页`}
                aria-current={item === current ? 'page' : undefined}
                onClick={() => onChange(item)}
              >
                {item}
              </Button>
            ),
          )}
          <Button
            variant="outline"
            size="icon"
            className="size-9 shrink-0"
            disabled={current >= totalPages}
            onClick={() => onChange(current + 1)}
          >
            <ChevronRight />
            <span className="sr-only">下一页</span>
          </Button>
        </div>
      ) : null}
    </div>
  )
}
