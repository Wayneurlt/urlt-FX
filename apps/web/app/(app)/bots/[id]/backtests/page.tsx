import { WorkspaceBacktests } from "@/features/bots/workspace";

export const metadata = { title: "Bot Backtests" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceBacktests botId={decodeURIComponent(id)} />;
}
