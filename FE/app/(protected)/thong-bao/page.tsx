import type { Metadata } from "next";
import { NotificationsPage } from "@/components/notifications-page";

export const metadata: Metadata = { title: "Kết nối thông báo" };

export default function Page() {
  return <NotificationsPage />;
}
