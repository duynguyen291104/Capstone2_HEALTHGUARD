import type { Metadata } from "next";
import { CaregiverRegisterForm } from "@/components/auth-forms";

export const metadata: Metadata = { title: "Tham gia nhóm chăm sóc" };

export default async function JoinPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const { token = "" } = await searchParams;
  return <CaregiverRegisterForm token={token} />;
}

