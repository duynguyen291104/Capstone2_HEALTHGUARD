import type { Metadata } from "next";
import { AccountSettings } from "@/components/account-settings";

export const metadata: Metadata = { title: "Cài đặt tài khoản" };

export default function SettingsPage() {
  return <AccountSettings />;
}
