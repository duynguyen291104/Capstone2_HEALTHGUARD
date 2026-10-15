"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ApiError, authApi, getErrorMessage, GROUP_ACCESS_CHANGED_EVENT, markAuthChanged, SESSION_EXPIRED_EVENT, storeGroupId } from "@/lib/api";
import { RequestSequence } from "@/lib/workflow";
import { Button, ErrorNotice } from "@/components/ui";
import type { CurrentUser } from "@/lib/types";

type AuthContextValue = {
  user: CurrentUser | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  setUser: (user: CurrentUser | null) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const userRef = useRef<CurrentUser | null>(null);
  const requests = useRef(new RequestSequence());
  const pathname = usePathname();

  const setUser = useCallback((next: CurrentUser | null) => {
    requests.current.invalidate();
    markAuthChanged();
    userRef.current = next;
    setUserState(next);
    setLoading(false);
    setError("");
    storeGroupId(next?.current_group?.care_group_id ?? null);
  }, []);

  const refresh = useCallback(async () => {
    const version = requests.current.start();
    if (!userRef.current) setLoading(true);
    setError("");
    try {
      const next = await authApi.me();
      if (!requests.current.isCurrent(version)) return;
      if (userRef.current?.id !== next.id || userRef.current?.current_group?.care_group_id !== next.current_group?.care_group_id) markAuthChanged();
      userRef.current = next;
      setUserState(next);
      storeGroupId(next.current_group?.care_group_id ?? null);
    } catch (caught) {
      if (requests.current.isCurrent(version)) {
        if (caught instanceof ApiError && caught.status === 401) {
          userRef.current = null;
          setUserState(null);
          storeGroupId(null);
        } else setError(getErrorMessage(caught));
      }
      throw caught;
    } finally {
      if (requests.current.isCurrent(version)) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const tracker = requests.current;
    const timer = window.setTimeout(() => { void refresh().catch(() => {}); }, 0);
    return () => { window.clearTimeout(timer); tracker.invalidate(); };
  }, [pathname, refresh]);

  useEffect(() => {
    const tracker = requests.current;
    const refreshQuietly = () => { void refresh().catch(() => {}); };
    const visible = () => { if (document.visibilityState === "visible") refreshQuietly(); };
    const expired = () => setUser(null);
    window.addEventListener("focus", refreshQuietly);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    window.addEventListener(GROUP_ACCESS_CHANGED_EVENT, refreshQuietly);
    return () => {
      tracker.invalidate();
      window.removeEventListener("focus", refreshQuietly);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
      window.removeEventListener(GROUP_ACCESS_CHANGED_EVENT, refreshQuietly);
    };
  }, [refresh, setUser]);

  const value = useMemo(
    () => ({ user, loading, error, refresh, setUser }),
    [user, loading, error, refresh, setUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, error, refresh } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user && !error) {
      const next = `${pathname}${window.location.search}`;
      router.replace(`/dang-nhap?next=${encodeURIComponent(next)}`);
    }
  }, [error, loading, pathname, router, user]);

  if (!loading && !user && error) return <main className="centered-page"><ErrorNotice message={error} /><Button type="button" onClick={() => { void refresh().catch(() => {}); }}>Thử kết nối lại</Button></main>;

  if (loading || !user) {
    return (
      <main className="centered-page" aria-live="polite">
        <div className="loading-mark" aria-hidden="true" />
        <p>Đang kiểm tra phiên đăng nhập…</p>
      </main>
    );
  }

  return children;
}

export function GroupGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !user.current_group) {
      router.replace("/tao-nhom");
    }
  }, [loading, router, user]);

  if (loading || !user?.current_group) {
    return (
      <main className="centered-page" aria-live="polite">
        <div className="loading-mark" aria-hidden="true" />
        <p>Đang chuẩn bị nhóm chăm sóc…</p>
      </main>
    );
  }

  return children;
}
