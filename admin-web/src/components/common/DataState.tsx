import { Button } from '@/components/ui/button'

interface DataStateProps {
  title: string
  description?: string
  onRetry?: () => void
}

export function DataState({ title, description, onRetry }: DataStateProps) {
  return (
    <div className="admin-state">
      <span className="mb-1 h-1 w-8 rounded-sm bg-[#b6c7bb]" aria-hidden="true" />
      <strong>{title}</strong>
      {description && <span>{description}</span>}
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>重新加载</Button>
      )}
    </div>
  )
}
