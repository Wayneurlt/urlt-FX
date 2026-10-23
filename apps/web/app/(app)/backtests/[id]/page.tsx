import { BacktestDetailView } from "@/features/backtests/backtest-detail";

export const metadata = { title: "Backtest" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BacktestDetailView backtestId={decodeURIComponent(id)} />;
}
