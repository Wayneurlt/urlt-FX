import { WorkspaceTrades } from "@/features/bots/workspace";

export const metadata = { title: "Bot Trades" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceTrades botId={decodeURIComponent(id)} />;
}
