import { WorkspaceActivity } from "@/features/bots/workspace";

export const metadata = { title: "Bot Activity" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceActivity botId={decodeURIComponent(id)} />;
}
