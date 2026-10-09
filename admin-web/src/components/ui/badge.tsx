import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 py-1 text-[11px] font-medium leading-none transition-colors before:size-1.5 before:rounded-full before:bg-current',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-[#dce4df] bg-[#f3f6f3] text-[#5f7167]',
        success: 'border-[#cae3d5] bg-[#edf6ef] text-[#27724f]',
        warning: 'border-[#eddfbf] bg-[#fcf6e9] text-[#8b6628]',
        destructive: 'border-[#efcecd] bg-[#fdf0ee] text-[#a6403d]',
        info: 'border-[#cedee6] bg-[#eff5f8] text-[#316b86]',
        outline: 'text-foreground',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
