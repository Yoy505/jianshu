const icpNumber = import.meta.env.VITE_ICP_NUMBER?.trim() || '津ICP备2026014961号-1'
export const hasIcpNumber = Boolean(icpNumber)

export function IcpLink({ className = '' }: { className?: string }) {
  if (!icpNumber) return null

  return (
    <a
      className={className}
      href="https://beian.miit.gov.cn/"
      target="_blank"
      rel="noopener noreferrer"
    >
      {icpNumber}
    </a>
  )
}
