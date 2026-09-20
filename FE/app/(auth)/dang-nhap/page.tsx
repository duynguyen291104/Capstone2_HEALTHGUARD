import type { Metadata } from "next";
import { LoginForm } from "@/components/auth-forms";

export const metadata: Metadata = { title: "Đăng nhập" };

export default function LoginPage() {
  return <LoginForm />;
}

