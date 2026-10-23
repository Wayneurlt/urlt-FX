"use client";
import { useQuery } from "@tanstack/react-query";
import { systemApi } from "@/lib/api/endpoints";
import { qk, REFETCH } from "@/lib/query-keys";

/** Header bar data; polled every 5s. */
export function useSystemStatus() {
  return useQuery({
    queryKey: qk.status,
    queryFn: systemApi.status,
    refetchInterval: REFETCH.status,
    refetchIntervalInBackground: false,
  });
}
