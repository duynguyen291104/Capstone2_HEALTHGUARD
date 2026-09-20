import type { Metadata } from "next";
import { OwnerRegisterForm } from "@/components/auth-forms";

export const metadata: Metadata = { title: "Tạo nhóm chăm sóc" };

export default function RegisterPage() {
  return <OwnerRegisterForm />;
}

