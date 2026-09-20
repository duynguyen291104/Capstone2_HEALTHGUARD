import type { Metadata } from "next";
import { TodayPage } from "@/components/today-page";

export const metadata: Metadata = { title: "Lịch hôm nay" };

export default function Page() {
  return <TodayPage />;
}

