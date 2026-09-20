"use client";

import {
  BellRing,
  CalendarClock,
  ChevronDown,
  ClipboardCheck,
  LogOut,
  Menu,
  Pill,
  UserRoundCog,
  UsersRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { authApi } from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { Brand } from "@/components/brand";

const navItems = [
  { href: "/hom-nay", label: "Hôm nay", icon: ClipboardCheck },
  { href: "/nguoi-duoc-cham-soc", label: "Người được chăm sóc", shortLabel: "Hồ sơ", icon: UsersRound },
  { href: "/nguoi-cham-soc", label: "Người chăm sóc", shortLabel: "Chăm sóc", icon: UserRoundCog, ownerOnly: true },
  { href: "/lich-thuoc", label: "Thuốc & lịch uống", shortLabel: "Lịch thuốc", icon: Pill },
  { href: "/thong-bao", label: "Kết nối thông báo", shortLabel: "Thông báo", icon: BellRing },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, setUser } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);
  const mobileMenuReturnFocusRef = useRef<HTMLElement | null>(null);
  const currentGroup = user?.current_group;
  const isOwner = currentGroup?.role === "OWNER";
  const visibleNav = navItems.filter((item) => !item.ownerOnly || isOwner);
  const mobileSidebarHidden = isMobile && !mobileOpen;

  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 860px)");

    function updateMobileState(event: MediaQueryList | MediaQueryListEvent) {
      setIsMobile(event.matches);
      if (!event.matches) setMobileOpen(false);
    }

    updateMobileState(mediaQuery);
    mediaQuery.addEventListener("change", updateMobileState);
    return () => mediaQuery.removeEventListener("change", updateMobileState);
  }, []);

  useEffect(() => {
    if (!isMobile || !mobileOpen) return;

    const sidebar = sidebarRef.current;
    const focusFrame = window.requestAnimationFrame(() => mobileCloseButtonRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileOpen(false);
        return;
      }

      if (event.key !== "Tab" || !sidebar) return;

      const focusableElements = Array.from(
        sidebar.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      ).filter((element) => element.getClientRects().length > 0);

      if (focusableElements.length === 0) {
        event.preventDefault();
        sidebar.focus();
        return;
      }

      const firstElement = focusableElements[0];
      const lastElement = focusableElements[focusableElements.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey && (activeElement === firstElement || !sidebar.contains(activeElement))) {
        event.preventDefault();
        lastElement.focus();
      } else if (!event.shiftKey && (activeElement === lastElement || !sidebar.contains(activeElement))) {
        event.preventDefault();
        firstElement.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      const returnTarget = mobileMenuReturnFocusRef.current;
      if (returnTarget?.isConnected && returnTarget.getClientRects().length > 0) returnTarget.focus();
    };
  }, [isMobile, mobileOpen]);

  function openMobileMenu() {
    mobileMenuReturnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : mobileMenuButtonRef.current;
    setMobileOpen(true);
  }

  async function logout() {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      router.replace("/dang-nhap");
    }
  }

  return (
    <div className="app-frame">
      <aside
        ref={sidebarRef}
        id="mobile-navigation"
        className={`sidebar ${mobileOpen ? "sidebar--open" : ""}`}
        aria-label="Menu chính"
        aria-hidden={mobileSidebarHidden || undefined}
        inert={mobileSidebarHidden || undefined}
        tabIndex={-1}
      >
        <div className="sidebar__top">
          <Brand href="/hom-nay" />
          <button ref={mobileCloseButtonRef} className="icon-button sidebar__close" type="button" onClick={() => setMobileOpen(false)} aria-label="Đóng menu">
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <div className="group-pill">
          <span className="group-pill__icon"><CalendarClock size={18} aria-hidden="true" /></span>
          <span>
            <small>Nhóm chăm sóc</small>
            <strong>{currentGroup?.care_group_name}</strong>
          </span>
        </div>
        <nav className="sidebar__nav" aria-label="Điều hướng chính">
          {visibleNav.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "nav-link nav-link--active" : "nav-link"}
                aria-current={active ? "page" : undefined}
                onClick={() => setMobileOpen(false)}
              >
                <Icon size={20} aria-hidden="true" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar__support">
          <p>Cần nhớ</p>
          <span>“Chưa xác nhận” không có nghĩa là đã bỏ thuốc. Hãy kiểm tra trực tiếp khi cần.</span>
        </div>
      </aside>

      {mobileOpen ? <button className="mobile-overlay" aria-label="Đóng menu" tabIndex={-1} onClick={() => setMobileOpen(false)} /> : null}

      <div className="app-main" aria-hidden={isMobile && mobileOpen || undefined} inert={isMobile && mobileOpen || undefined}>
        <header className="topbar">
          <button
            ref={mobileMenuButtonRef}
            className="icon-button topbar__menu"
            type="button"
            onClick={openMobileMenu}
            aria-label="Mở menu"
            aria-controls="mobile-navigation"
            aria-expanded={mobileOpen}
          >
            <Menu size={21} aria-hidden="true" />
          </button>
          <div className="topbar__mobile-brand"><Brand href="/hom-nay" /></div>
          <div className="profile-menu">
            <button className="profile-button" type="button" aria-expanded={profileOpen} onClick={() => setProfileOpen((value) => !value)}>
              <span className="avatar">{user?.full_name.trim().charAt(0).toUpperCase()}</span>
              <span className="profile-button__text">
                <strong>{user?.full_name}</strong>
                <small>{isOwner ? "Chủ gia đình" : "Người chăm sóc"}</small>
              </span>
              <ChevronDown size={16} aria-hidden="true" />
            </button>
            {profileOpen ? (
              <div className="profile-popover">
                <p>{user?.email}</p>
                <button type="button" onClick={logout}>
                  <LogOut size={17} aria-hidden="true" /> Đăng xuất
                </button>
              </div>
            ) : null}
          </div>
        </header>
        <main className="content">{children}</main>
      </div>

      <nav
        className="bottom-nav"
        aria-label="Điều hướng trên điện thoại"
        aria-hidden={isMobile && mobileOpen || undefined}
        inert={isMobile && mobileOpen || undefined}
      >
        {visibleNav.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} className={active ? "bottom-nav__link bottom-nav__link--active" : "bottom-nav__link"}>
              <Icon size={19} aria-hidden="true" />
              <span>{item.shortLabel ?? item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
