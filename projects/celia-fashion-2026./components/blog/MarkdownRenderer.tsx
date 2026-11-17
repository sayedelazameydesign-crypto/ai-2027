import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";

export const MarkdownRenderer = ({ content, className }: { content: string; className?: string }) => {
    return (
        <div className={cn("prose prose-invert max-w-none text-zinc-300", className)}>
            <ReactMarkdown
                components={{
                    h1: ({ children }) => <h1 className="text-4xl font-black mt-12 mb-6 text-white uppercase">{children}</h1>,
                    h2: ({ children }) => <h2 className="text-2xl font-bold mt-10 mb-4 text-accent uppercase tracking-tight">{children}</h2>,
                    p: ({ children }) => <p className="mb-6 leading-relaxed text-lg">{children}</p>,
                    li: ({ children }) => <li className="mb-2 list-disc list-inside">{children}</li>,
                    blockquote: ({ children }) => (
                        <blockquote className="border-l-4 border-accent pl-6 my-8 italic text-zinc-400 text-xl font-light py-2 bg-accent/5 rounded-r-lg">
                            {children}
                        </blockquote>
                    ),
                    code: ({ children }) => (
                        <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-accent font-mono text-sm">
                            {children}
                        </code>
                    ),
                }}
            >
                {content}
            </ReactMarkdown>
        </div>
    );
};
