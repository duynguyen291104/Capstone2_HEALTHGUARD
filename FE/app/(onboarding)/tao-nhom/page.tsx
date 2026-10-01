import type { Metadata } from "next";
import { CreateGroupPage } from "@/components/create-group-page";

export const metadata: Metadata = { title: "Tạo nhóm chăm sóc" };

export default function Page() {
  return <CreateGroupPage />;
}
