import type { Metadata } from "next";
import { FallDetectionPage } from "@/components/fall-detection-page";

export const metadata: Metadata = { title: "Phát hiện té ngã" };

export default function Page() {
  return <FallDetectionPage />;
}
