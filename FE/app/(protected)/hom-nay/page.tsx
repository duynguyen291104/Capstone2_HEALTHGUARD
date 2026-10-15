import type { Metadata } from "next";
import { TodayPage } from "@/components/today-page";

export const metadata: Metadata = { title: "Lịch hôm nay" };

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const occurrence = Array.isArray(params.occurrence) ? params.occurrence[0] ?? "" : params.occurrence ?? "";
  const requestedDate = Array.isArray(params.date) ? params.date[0] : params.date;
  const initialDate = isValidDate(requestedDate) ? requestedDate : undefined;

  return (
    <TodayPage
      key={`${initialDate ?? "today"}-${occurrence}`}
      highlightedId={occurrence}
      initialDate={initialDate}
    />
  );
}

function isValidDate(value: string | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month - 1
    && parsed.getUTCDate() === day;
}
