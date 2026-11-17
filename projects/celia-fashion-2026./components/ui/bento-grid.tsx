import React from "react";
import { cn } from "@/lib/utils";

export const BentoGrid = ({
    className,
    children,
}: {
    className?: string;
    children?: React.ReactNode;
}) => {
    return (
        <div
            className={cn(
                "grid md:auto-rows-[18rem] grid-cols-1 md:grid-cols-3 gap-4 max-w-7xl mx-auto ",
                className
            )}
        >
            {children}
        </div>
    );
};

export const BentoGridItem = ({
    className,
    title,
    description,
    header,
    icon,
    onClick,
}: {
    className?: string;
    title?: string | React.ReactNode;
    description?: string | React.ReactNode;
    header?: React.ReactNode;
    icon?: React.ReactNode;
    onClick?: () => void;
}) => {
    return (
        <div
            className={cn(
                "row-span-1 border border-zinc-200 dark:border-zinc-800 rounded-xl group/bento hover:shadow-xl transition duration-300 shadow-input dark:shadow-none p-4 bg-white dark:bg-zinc-950 justify-between flex flex-col space-y-4 hover:border-accent group-hover:bg-zinc-900",
                className
            )}
            onClick={onClick}
        >
            {header}
            <div className="group-hover/bento:translate-x-2 transition duration-200">
                {icon}
                <div className="font-heading font-bold text-zinc-900 dark:text-zinc-100 mb-2 mt-2 text-xl">
                    {title}
                </div>
                <div className="font-sans font-normal text-zinc-600 text-sm dark:text-zinc-400">
                    {description}
                </div>
            </div>
        </div>
    );
};
