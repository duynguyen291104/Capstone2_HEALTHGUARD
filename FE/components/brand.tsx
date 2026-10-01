import { HeartPulse } from "lucide-react";
import Link from "next/link";

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="brand" aria-label="HealthGuard - Trang chủ">
      <span className="brand__mark"><HeartPulse size={22} strokeWidth={2.5} aria-hidden="true" /></span>
      <span>
        <strong>HealthGuard</strong>
        <small>Chăm sóc đúng lúc</small>
      </span>
    </Link>
  );
}

