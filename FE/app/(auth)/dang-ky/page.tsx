import type { Metadata } from "next";
import { AccountRegisterForm } from "@/components/auth-forms";

export const metadata: Metadata = { title: "Tạo tài khoản" };

export default function RegisterPage() {
  return <AccountRegisterForm />;
}
