import { CycleDetailView } from "@/features/trades/cycle-detail";

export const metadata = { title: "Cycle" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CycleDetailView cycleId={decodeURIComponent(id)} />;
}
