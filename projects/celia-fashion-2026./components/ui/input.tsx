import * as React from "react"

import { cn } from "@/lib/utils"

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-lg border border-[#0b12341a] bg-white px-3 py-2 text-sm text-[#364050] shadow-[0px_0px_0px_1px_rgba(11,18,52,0.10),0px_1px_2px_rgba(11,18,52,0.15)] file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-[#696f8c] placeholder:text-[14px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }