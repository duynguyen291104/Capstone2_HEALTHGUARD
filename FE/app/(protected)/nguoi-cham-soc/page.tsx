import type { Metadata } from "next";
import { CaregiversPage } from "@/components/caregivers-page";

export const metadata: Metadata = { title: "Người chăm sóc" };

export default function Page() {
  return <CaregiversPage />;
}

