import { WorkspacePositions } from "@/features/bots/workspace";

export const metadata = { title: "Bot Positions" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspacePositions botId={decodeURIComponent(id)} />;
}
