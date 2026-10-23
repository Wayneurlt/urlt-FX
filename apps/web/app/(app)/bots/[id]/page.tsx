import { WorkspaceOverview } from "@/features/bots/workspace";

export const metadata = { title: "Bot workspace" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceOverview botId={decodeURIComponent(id)} />;
}
