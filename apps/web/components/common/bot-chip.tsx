import Link from "next/link";
import { botIdentity, workspaceHref } from "@/lib/bots";
import { cn } from "@/lib/utils";

/** Coloured bot label. Shown on every row that belongs to a bot so the two bots are never mixed up. */
export function BotChip({
  bot,
  link = true,
  className,
}: {
  /** bot id or bot kind */
  bot: string | null | undefined;
  link?: boolean;
  className?: string;
}) {
  const id = botIdentity(bot);
  const Icon = id.icon;
  const body = (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        id.chip,
        className,
      )}
    >
      <Icon className="size-3" aria-hidden="true" />
      {id.short}
    </span>
  );
  if (!link || !bot) return body;
  return (
    <Link href={workspaceHref(id.id)} className="hover:opacity-80" title={`Open the ${id.short} workspace`}>
      {body}
    </Link>
  );
}
