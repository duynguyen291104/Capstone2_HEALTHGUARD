"use client";

import { Camera, Film, History, RefreshCw, Send } from "lucide-react";
import Link from "next/link";
import { useAuth } from "@/components/auth-provider";
import { useEffect, useRef, useState } from "react";
import { Button, ErrorNotice, PageHeader } from "@/components/ui";
import { API_URL, eldersApi, fallApi, getErrorMessage, type FallEvent, type FallVideoResult } from "@/lib/api";
import type { Elder } from "@/lib/types";

const labels: Record<string, string> = {
  WARMING_UP: "Đang thu đủ khung hình",
  NORMAL: "Theo dõi",
  POSSIBLE_FALL: "Nghi ngờ té ngã",
  VERIFYING: "Đang kiểm tra",
  CONFIRMED_FALL: "AI ghi nhận té ngã",
  RECOVERED: "Đã hồi phục tư thế",
};
const deliveryLabels: Record<string, string> = {
  suppressed: "Không gửi (chế độ thử)",
  pending: "Đang xử lý",
  disabled: "Telegram đang tắt",
  no_recipients: "Chủ nhóm chưa liên kết Telegram",
  failed: "Gửi thất bại",
  partial: "Gửi được một phần",
  sent: "Đã gửi qua bot",
};
const suffixes = [".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"];

function FallWorkspace({ elder, locked, onBusy }: { elder: Elder; locked: boolean; onBusy: (busy: boolean) => void }) {
  const { user } = useAuth();
  const id = elder.id;
  const [ready, setReady] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [history, setHistory] = useState<FallEvent[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const [cameraIndex, setCameraIndex] = useState("0");
  const [deviceCode, setDeviceCode] = useState("");
  const [preview, setPreview] = useState("");
  const [cameraState, setCameraState] = useState("WARMING_UP");
  const [notify, setNotify] = useState(false);
  const [sample, setSample] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<FallVideoResult | null>(null);
  const [pending, setPending] = useState<"start" | "stop" | "video" | null>(null);
  const [checking, setChecking] = useState(false);
  const alive = useRef(false);
  const sessionRef = useRef<string | null>(null);
  const historyRequest = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      const owned = sessionRef.current;
      if (owned) void fallApi.camera(id, owned, "stop").catch(() => {});
    };
  }, [id]);

  useEffect(() => {
    let active = true;
    fallApi.health(id).then(
      (value) => { if (active) setReady(value.ready); },
      (caught) => { if (active) { setReady(false); setError(getErrorMessage(caught)); } },
    );
    const version = ++historyRequest.current;
    fallApi.history(id).then(
      (data) => { if (active && version === historyRequest.current) setHistory(data); },
      (caught) => { if (active && version === historyRequest.current) setHistoryError(getErrorMessage(caught)); },
    );
    return () => { active = false; };
  }, [id]);

  async function refreshHistory() {
    const version = ++historyRequest.current;
    try {
      const data = await fallApi.history(id);
      if (alive.current && version === historyRequest.current) { setHistory(data); setHistoryError(""); }
    } catch (caught) {
      if (alive.current && version === historyRequest.current) setHistoryError(getErrorMessage(caught));
    }
  }

  async function check() {
    if (checking) return;
    setChecking(true);
    setError("");
    try { setReady((await fallApi.health(id)).ready); }
    catch (caught) { setReady(false); setError(getErrorMessage(caught)); }
    finally { setChecking(false); }
  }

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let historyTimer: ReturnType<typeof setTimeout>;
    let state = "";
    async function poll() {
      try {
        const data = await fallApi.camera(id, session!, "status", controller.signal);
        if (controller.signal.aborted) return;
        setPreview(data.jpeg ?? "");
        setCameraState(data.state);
        if (data.status === "failed" || data.status === "stopped") {
          setPreview("");
          setSession(null);
          sessionRef.current = null;
          onBusy(false);
          if (data.error) setError(data.error);
          return;
        }
        if (data.state === "CONFIRMED_FALL" && state !== data.state) {
          clearTimeout(historyTimer);
          historyTimer = setTimeout(() => {
            const version = ++historyRequest.current;
            void fallApi.history(id).then((items) => {
              if (!controller.signal.aborted && version === historyRequest.current) setHistory(items);
            }).catch(() => {});
          }, 1500);
        }
        state = data.state;
        timer = setTimeout(poll, 220);
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(getErrorMessage(caught));
          setPreview("");
          setSession(null);
          sessionRef.current = null;
          onBusy(false);
          void fallApi.camera(id, session!, "stop").catch(() => {});
        }
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); clearTimeout(historyTimer); };
  }, [id, session, onBusy]);

  async function startCamera() {
    if (locked || !ready) return;
    setPending("start");
    onBusy(true);
    setError("");
    setCameraState("WARMING_UP");
    try {
      const { session_id } = await fallApi.startCamera(id, Number(cameraIndex), notify, deviceCode.trim());
      if (!alive.current) { void fallApi.camera(id, session_id, "stop").catch(() => {}); return; }
      sessionRef.current = session_id;
      setSession(session_id);
    } catch (caught) {
      if (alive.current) { setError(getErrorMessage(caught)); onBusy(false); }
    } finally {
      if (alive.current) setPending(null);
    }
  }

  async function stopCamera() {
    if (!session || pending) return;
    const owned = session;
    setPending("stop");
    setSession(null);
    setPreview("");
    try { await fallApi.camera(id, owned, "stop"); }
    catch (caught) { setError(`${getErrorMessage(caught)} Camera sẽ tự dừng khi không còn kết nối.`); }
    finally { sessionRef.current = null; setPending(null); onBusy(false); }
    void refreshHistory();
  }

  async function analyze(event: React.FormEvent) {
    event.preventDefault();
    if (locked || !ready || !file) return;
    const suffix = file.name.match(/\.[a-z0-9]+$/i)?.[0].toLowerCase() ?? "";
    if (!suffixes.includes(suffix) || file.size === 0 || file.size > 50 * 1024 * 1024) {
      setError("Hãy chọn video đúng định dạng, có dữ liệu và tối đa 50 MB.");
      return;
    }
    setPending("video");
    onBusy(true);
    setResult(null);
    setError("");
    try {
      const data = await fallApi.video(id, file, suffix, sample, notify);
      if (alive.current) { setResult(data); void refreshHistory(); }
    } catch (caught) {
      if (alive.current) setError(getErrorMessage(caught));
    } finally {
      if (alive.current) { setPending(null); onBusy(false); }
    }
  }

  return <>
    <div className="fall-status">
      <span role="status">{ready === null ? "Đang kiểm tra AI…" : ready ? "Dịch vụ FallGuard đã sẵn sàng" : "Dịch vụ FallGuard chưa sẵn sàng"}</span>
      <Button type="button" variant="secondary" loading={checking} disabled={locked} onClick={check}><RefreshCw size={16} /> Kiểm tra lại</Button>
    </div>
    {ready === false && <p className="fall-note">Chạy <code>.\run-healthguard.cmd --with-ai</code>. Nếu dùng <code>--local-only</code>, bot không gửi Telegram.</p>}
    {error && <ErrorNotice message={error} />}
    <div className="info-strip"><Send size={18} /><div>
      <label><input type="checkbox" checked={notify} disabled={locked} onChange={(event) => setNotify(event.target.checked)} /> Gửi cảnh báo thật qua Telegram khi AI ghi nhận té ngã</label>
      <p>Chỉ gửi tin nhắn cho chủ nhóm đã liên kết Telegram. Không gửi hình ảnh. {user?.current_group?.role === "OWNER" && <Link className="text-link" href="/thong-bao">Quản lý liên kết Telegram</Link>}</p>
    </div></div>
    <div className="fall-grid">
      <section className="fall-card">
        <div className="fall-card__heading"><span className="feature-icon"><Camera size={22} /></span><div><h2>Camera nhận diện trực tiếp</h2><p>Dùng webcam gắn vào máy đang chạy HealthGuard.</p></div></div>
        <div className="fall-preview">
          {preview
            // Data URL belongs to the protected, short-lived camera session.
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={`data:image/jpeg;base64,${preview}`} alt="Khung hình camera với điểm khớp do FallGuard đánh dấu" />
            : <div><Camera size={30} /><p>{session || pending === "start" ? "Đang mở camera AI…" : "Camera đang tắt"}</p></div>}
        </div>
        {session && <p role="status">{labels[cameraState] ?? cameraState}</p>}
        <label className="field"><span className="field__label">Số camera trên máy chủ (0–9)</span><input className="field__control" type="number" min={0} max={9} step={1} value={cameraIndex} disabled={locked} onChange={(event) => setCameraIndex(event.target.value)} /></label>
        <label className="field"><span className="field__label">Mã bật camera trên PowerShell</span><input className="field__control" type="password" autoComplete="off" maxLength={64} value={deviceCode} disabled={locked} onChange={(event) => setDeviceCode(event.target.value)} /></label>
        <div className="fall-actions">
          {session || pending === "stop"
            ? <Button type="button" variant="secondary" loading={pending === "stop"} onClick={stopCamera}>Tắt camera</Button>
            : <Button type="button" disabled={!ready || locked || deviceCode.trim().length < 8 || !/^\d$/.test(cameraIndex)} loading={pending === "start"} onClick={startCamera}>Bật camera AI</Button>}
        </div>
        <p className="fall-note">Không mở đồng thời cửa sổ camera FallGuard cũ. Giữ trang mở trong khi theo dõi; camera tự dừng nếu mất kết nối.</p>
      </section>
      <section className="fall-card">
        <div className="fall-card__heading"><span className="feature-icon feature-icon--blue"><Film size={22} /></span><div><h2>Phân tích video</h2><p>Video tối đa 50 MB và 120 giây.</p></div></div>
        <form className="form-stack" onSubmit={analyze}>
          <label className="field"><span className="field__label">Chọn video</span><input className="field__control" type="file" accept={suffixes.join(",")} required disabled={locked} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); }} /></label>
          <label><input type="checkbox" checked={sample} disabled={locked} onChange={(event) => setSample(event.target.checked)} /> Video mẫu URFD (tách nửa hình RGB, không gửi Telegram)</label>
          <Button type="submit" loading={pending === "video"} disabled={!ready || locked || !file}>Phân tích video</Button>
        </form>
        {result && <div className="fall-result" role="status">
          <h3>{result.event_ids.length ? `AI ghi nhận ${result.event_ids.length} sự kiện té ngã`
            : result.predictions ? "Không phát hiện sự kiện té ngã trong đoạn đã xử lý" : "Không đủ dữ liệu để đưa ra kết quả"}</h3>
          <p>Đọc {result.frames} khung hình; nhận được tư thế ở {result.pose_frames} khung đã lấy mẫu.</p>
          {result.bounded && <p>AI chỉ xử lý một phần clip do giới hạn thời lượng.</p>}
          {result.video_path && <><video controls playsInline preload="metadata" crossOrigin="use-credentials" src={API_URL + result.video_path} aria-label="Video kết quả FallGuard" /><a className="text-link" href={API_URL + result.video_path} target="_blank" rel="noreferrer">Mở video kết quả</a></>}
          <p className="fall-note">Video kết quả tạm thời. Nếu trình duyệt không phát được, mở riêng để tải và xem bằng trình phát hỗ trợ.</p>
        </div>}
      </section>
    </div>
    <section className="fall-card fall-dashboard">
      <div className="fall-card__heading"><span className="feature-icon feature-icon--peach"><History size={22} /></span><div><h2>Lịch sử AI ghi nhận té ngã</h2><p>Chỉ hiển thị các sự kiện của hồ sơ đang chọn.</p></div></div>
      <Button type="button" variant="secondary" onClick={() => void refreshHistory()}><RefreshCw size={16} /> Làm mới lịch sử</Button>
      {historyError && <ErrorNotice message={historyError} />}
      {!historyError && (history.length ? <ul className="fall-history">{history.map((item) => <li key={item.id}>
        <strong>{item.source === "camera" ? "Camera trực tiếp" : "Video tải lên"}</strong>
        <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}</time>
        <span>{deliveryLabels[item.telegram_status] ?? item.telegram_status}{item.sent || item.failed ? ` · ${item.sent} gửi được, ${item.failed} lỗi` : ""}</span>
      </li>)}</ul> : <p className="fall-note">Chưa có sự kiện nào cho hồ sơ này.</p>)}
    </section>
  </>;
}

export function FallDetectionPage() {
  const [elders, setElders] = useState<Elder[]>([]);
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  useEffect(() => {
    let active = true;
    eldersApi.list().then((items) => {
      if (!active) return;
      const available = items.filter((elder) => elder.is_active);
      setElders(available);
      setId(available[0]?.id ?? "");
    }).catch((caught) => { if (active) setError(getErrorMessage(caught)); });
    return () => { active = false; };
  }, []);
  const elder = elders.find((item) => item.id === id);
  return <>
    <PageHeader title="Phát hiện té ngã" description="Mở camera AI, phân tích video và xem cảnh báo theo từng hồ sơ chăm sóc." />
    {error && <ErrorNotice message={error} />}
    <div className="toolbar-card">
      <label className="field"><span className="field__label">Người được theo dõi</span><select className="field__control" disabled={locked || !elders.length} value={id} onChange={(event) => setId(event.target.value)}>
        {!elders.length && <option value="">Chưa có hồ sơ khả dụng</option>}
        {elders.map((item) => <option key={item.id} value={item.id}>{item.full_name}</option>)}
      </select></label>
      <p>Chọn đúng hồ sơ trước khi bật camera hoặc phân tích video.</p>
    </div>
    {elder && <FallWorkspace key={elder.id} elder={elder} locked={locked} onBusy={setLocked} />}
    <p className="fall-note">FallGuard có thể bỏ sót hoặc báo nhầm. Kết quả dùng để hỗ trợ kiểm tra trực tiếp, không thay thế chăm sóc hay cấp cứu.</p>
  </>;
}
