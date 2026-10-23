import { BotWorkspaceShell } from "@/features/bots/workspace";

export default async function BotWorkspaceLayout({
  params,
  children,
}: {
  params: Promise<{ id: string }>;
  children: React.ReactNode;
}) {
  const { id } = await params;
  return <BotWorkspaceShell botId={decodeURIComponent(id)}>{children}</BotWorkspaceShell>;
}
