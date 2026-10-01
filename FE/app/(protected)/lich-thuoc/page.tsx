import type { Metadata } from "next";
import { MedicationsPage } from "@/components/medications-page";

export const metadata: Metadata = { title: "Thuốc và lịch uống" };

export default function Page() {
  return <MedicationsPage />;
}

