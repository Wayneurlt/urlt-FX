import { WorkspaceConfiguration } from "@/features/bots/workspace";

export const metadata = { title: "Bot Configuration" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WorkspaceConfiguration botId={decodeURIComponent(id)} />;
}
