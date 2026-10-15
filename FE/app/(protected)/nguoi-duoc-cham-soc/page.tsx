import type { Metadata } from "next";
import { EldersPage } from "@/components/elders-page";

export const metadata: Metadata = { title: "Người được chăm sóc" };

export default function Page() {
  return <EldersPage />;
}

