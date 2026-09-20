import { AppShell } from "@/components/app-shell";
import { AuthGate, GroupGate } from "@/components/auth-provider";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <GroupGate>
        <AppShell>{children}</AppShell>
      </GroupGate>
    </AuthGate>
  );
}
