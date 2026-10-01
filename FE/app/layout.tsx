import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/components/auth-provider";

export const metadata: Metadata = {
  title: {
    default: "HealthGuard — Chăm sóc đúng lúc",
    template: "%s | HealthGuard",
  },
  description: "Quản lý hồ sơ và lịch dùng thuốc cho người cao tuổi.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}

