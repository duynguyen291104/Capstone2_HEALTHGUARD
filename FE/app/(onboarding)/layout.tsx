import { AuthGate } from "@/components/auth-provider";

export default function OnboardingLayout({ children }: { children: React.ReactNode }) {
  return <AuthGate>{children}</AuthGate>;
}
