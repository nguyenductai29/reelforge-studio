import { Fragment } from "react";
import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NodeKind } from "@/lib/workflow";
import { kindIcon } from "./kind-icon";

// A step is 28 px with a 12 px link before it, so the n-th (from 0) is drawn once the diagram is 40 (n + 1) px wide:
// a narrow card drops the last steps instead of clipping the first and the last. A container query on the diagram's
// own width, the classes written out for Tailwind to find them.
const FITS = ["", "@max-[80px]:hidden", "@max-[120px]:hidden", "@max-[160px]:hidden", "@max-[200px]:hidden",
              "@max-[240px]:hidden", "@max-[280px]:hidden", "@max-[320px]:hidden"];

export function MiniDiagram({
  kinds,
  branches = 0,
  className,
}: {
  kinds: NodeKind[];
  branches?: number | undefined;
  className?: string | undefined;
}) {
  if (kinds.length === 0) {
    return (
      <div
        className={cn(
          "flex h-24 items-center justify-center rounded-lg border border-dashed border-border-strong",
          className,
        )}
      >
        <Plus className="size-5 text-muted-foreground" />
      </div>
    );
  }
  // Long graphs keep the first steps so the diagram never overflows its card.
  const shown = kinds.length > 8 ? kinds.slice(0, 8) : kinds;
  const main = branches > 0 ? shown.slice(0, -1) : shown;
  const LastIcon = kindIcon[shown[shown.length - 1]!];

  return (
    <div
      className={cn(
        "@container flex h-24 items-center justify-center gap-0 overflow-hidden rounded-lg bg-[radial-gradient(circle,var(--border)_1px,transparent_1px)] bg-[size:12px_12px]",
        className,
      )}
    >
      {main.map((kind, index) => {
        const Icon = kindIcon[kind];
        // The branches at the end take the room of one step.
        const fit = FITS[index + (branches > 0 ? 1 : 0)];
        return (
          <Fragment key={index}>
            {index > 0 && <span className={cn("h-px w-3 shrink-0 bg-border-strong", fit)} />}
            <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-md border border-border-strong bg-card", fit)}>
              <Icon className="size-3.5 text-primary" />
            </span>
          </Fragment>
        );
      })}
      {branches > 0 && (
        <>
          <span className="h-px w-3 shrink-0 bg-border-strong" />
          <span className="flex flex-col gap-1 border-l border-border-strong py-1 pl-2">
            {Array.from({ length: branches }).map((_, index) => (
              <span
                key={index}
                className="flex size-4 items-center justify-center rounded border border-border-strong bg-card"
              >
                <LastIcon className="size-2.5 text-primary" />
              </span>
            ))}
          </span>
        </>
      )}
    </div>
  );
}
