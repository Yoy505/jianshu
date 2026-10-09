import { createRoot, type Root } from 'react-dom/client'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'

type ToastVariant = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  message: string
  variant: ToastVariant
}

/* 类名使用完整字面量，确保被 Tailwind 源码扫描识别 */
const VARIANT_CLASSES: Record<ToastVariant, string> = {
  success: 'border-emerald-200/80 bg-white/80 text-emerald-800',
  error: 'border-red-200/80 bg-white/80 text-red-800',
  info: 'border-zinc-200/80 bg-white/80 text-zinc-800',
}

const ICON_CLASSES: Record<ToastVariant, string> = {
  success: 'text-emerald-600',
  error: 'text-red-600',
  info: 'text-zinc-500',
}

function VariantIcon({ variant }: { variant: ToastVariant }) {
  if (variant === 'success') return <CheckCircle2 className="size-4 shrink-0" />
  if (variant === 'error') return <AlertCircle className="size-4 shrink-0" />
  return <Info className="size-4 shrink-0" />
}

let items: ToastItem[] = []
let seq = 0
let root: Root | null = null

function render(): void {
  if (!root) {
    const container = document.createElement('div')
    container.className =
      'pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2'
    document.body.appendChild(container)
    root = createRoot(container)
  }
  root.render(
    <div className="flex flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={`pointer-events-auto flex items-start gap-2.5 rounded-lg border px-3.5 py-2.5 shadow-[0_12px_36px_rgba(24,24,27,0.14)] backdrop-blur-xl ${VARIANT_CLASSES[item.variant]}`}
        >
          <span className={`mt-0.5 ${ICON_CLASSES[item.variant]}`}>
            <VariantIcon variant={item.variant} />
          </span>
          <p className="text-sm leading-5">{item.message}</p>
        </div>
      ))}
    </div>,
  )
}

function show(message: string, variant: ToastVariant): void {
  const last = items[items.length - 1]
  if (last && last.message === message && last.variant === variant) return
  const id = ++seq
  items = [...items.slice(-2), { id, message, variant }]
  render()
  window.setTimeout(() => {
    items = items.filter((item) => item.id !== id)
    render()
  }, 3800)
}

/** 轻量命令式 toast（挂载在 body，无需 Provider） */
export const toast = {
  success: (message: string): void => show(message, 'success'),
  error: (message: string): void => show(message, 'error'),
  info: (message: string): void => show(message, 'info'),
}
