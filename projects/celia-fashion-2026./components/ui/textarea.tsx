import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(({ className, ...props }, ref) => {
  return (
    <textarea
      className={cn(
        "flex min-h-[150px] w-full rounded-lg border border-[#0b12341a] bg-white px-3 py-2 text-[12px] text-[#696f8c] shadow-[0px_0px_0px_1px_rgba(11,18,52,0.10),0px_1px_2px_rgba(11,18,52,0.15)] placeholder:text-[#696f8c] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className
      )}
      ref={ref}
      {...props}
    />
  )
})
Textarea.displayName = "Textarea"

export { Textarea }