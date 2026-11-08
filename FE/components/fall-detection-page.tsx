"use client";

import { Camera, ExternalLink, Film, Radio, Send, Terminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, ErrorNotice, PageHeader } from "@/components/ui";

const aiUrl = "http://127.0.0.1:8001/";
const aiFolder = "cd .\\AI";
const dashboardCommand = ".\\.venv\\Scripts\\fallguard.exe serve --config .\\configs\\urfd_demo.yaml --checkpoint .\\artifacts\\urfd_preview_gru\\best.pt --port 8001";
const cameraCommand = ".\\.venv\\Scripts\\fallguard.exe infer camera --source 0 --checkpoint .\\artifacts\\urfd_preview_gru\\best.pt --no-telegram";

function CameraPreview() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const requestRef = useRef(0);
  const [status, setStatus] = useState<"idle" | "pending" | "preview">("idle");
  const [error, setError] = useState("");

  useEffect(() => () => {
    requestRef.current++;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  function stop() {
    requestRef.current++;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setStatus("idle");
  }

  async function start() {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Không truy cập được camera tại địa chỉ này. Hãy mở bằng localhost hoặc HTTPS.");
      return;
    }
    const request = ++requestRef.current;
    setStatus("pending");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      if (request !== requestRef.current || !videoRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      setStatus("preview");
    } catch (caught) {
      if (request !== requestRef.current) return;
      setStatus("idle");
      setError(caught instanceof DOMException && caught.name === "NotAllowedError"
        ? "Trình duyệt chưa được cấp quyền camera. Hãy kiểm tra quyền của trang."
        : "Không mở được webcam. Kiểm tra thiết bị hoặc đóng cửa sổ AI đang sử dụng camera.");
    }
  }

  return <section className="fall-card" aria-labelledby="camera-heading">
    <div className="fall-card__heading">
      <span className="feature-icon"><Camera size={22} aria-hidden="true" /></span>
      <div><h2 id="camera-heading">Kiểm tra webcam</h2><p>Xem hình tại chỗ, không ghi hoặc tải hình lên.</p></div>
    </div>
    <div className="fall-preview">
      <video ref={videoRef} autoPlay muted playsInline hidden={status !== "preview"} aria-label="Hình xem thử từ webcam" />
      {status !== "preview" && <div><Camera size={30} aria-hidden="true" /><p role="status">{status === "pending" ? "Đang chờ quyền camera…" : "Webcam đang tắt"}</p></div>}
    </div>
    {error && <ErrorNotice message={error} />}
    <div className="fall-actions">
      {status === "idle"
        ? <Button type="button" onClick={() => void start()}>Bật xem thử</Button>
        : <Button type="button" variant="secondary" onClick={stop}>{status === "pending" ? "Hủy yêu cầu" : "Tắt webcam"}</Button>}
    </div>
    <p className="fall-note">Không có nhận diện AI trong khung xem thử. Tắt webcam ở đây trước khi mở camera bằng FallGuard để tránh tranh thiết bị.</p>
  </section>;
}

export function FallDetectionPage() {
  const [showDashboard, setShowDashboard] = useState(false);

  return <>
    <PageHeader title="Phát hiện té ngã" description="Kiểm tra camera, phân tích video mẫu và xem kết quả FallGuard trong cùng khu vực HealthGuard." />
    <div className="fall-grid">
      <CameraPreview />
      <section className="fall-card" aria-labelledby="service-heading">
        <div className="fall-card__heading">
          <span className="feature-icon feature-icon--blue"><Radio size={22} aria-hidden="true" /></span>
          <div><h2 id="service-heading">Luồng nhận diện trực tiếp</h2><p>FallGuard xử lý trên máy đang gắn camera.</p></div>
        </div>
        <dl className="fall-flow">
          <div><dt>Đầu vào</dt><dd>Webcam hoặc camera USB trên máy chạy AI.</dd></div>
          <div><dt>Xử lý</dt><dd>AI theo dõi điểm khớp và kiểm tra tín hiệu té ngã qua nhiều khung hình.</dd></div>
          <div><dt>Đầu ra</dt><dd>Cửa sổ AI hiển thị khung hình chú thích. Sự kiện xác nhận được lưu trong FallGuard.</dd></div>
          <div><dt><Send size={15} aria-hidden="true" /> Telegram</dt><dd>FallGuard gửi cảnh báo nếu đã cấu hình bot và người nhận. Web này chưa đọc trạng thái gửi hay cấu hình bot AI.</dd></div>
        </dl>
        <details className="fall-guide">
          <summary><Terminal size={17} aria-hidden="true" /> Lệnh chạy webcam AI</summary>
          <p>Mở PowerShell tại thư mục gốc dự án trên máy gắn webcam. Lệnh thử không gửi Telegram. Nếu nhóm đã cấu hình bot AI và muốn gửi thật, bỏ <code>--no-telegram</code>.</p>
          <pre><code>{aiFolder}{"\n"}{cameraCommand}</code></pre>
          <p>Nhấn Q hoặc Esc trong cửa sổ camera AI để dừng.</p>
        </details>
      </section>
    </div>

    <section className="fall-card fall-dashboard" aria-labelledby="video-heading">
      <div className="fall-card__heading">
        <span className="feature-icon feature-icon--peach"><Film size={22} aria-hidden="true" /></span>
        <div><h2 id="video-heading">Phân tích video và lịch sử</h2><p>Tải clip mẫu, xem trạng thái, video có chú thích và lịch sử sự kiện trong bảng FallGuard.</p></div>
      </div>
      <p className="fall-note">Chỉ dùng để demo trên máy chạy AI. Dữ liệu trong bảng FallGuard chưa gắn với hồ sơ hay quyền nhóm HealthGuard. Chỉ dùng video mẫu, không tải video chăm sóc thật.</p>
      <details className="fall-guide" open={!showDashboard}>
        <summary><Terminal size={17} aria-hidden="true" /> Lệnh mở bảng FallGuard</summary>
        <p>Giữ HealthGuard chạy. Mở PowerShell khác tại thư mục gốc dự án trên cùng máy (cần cài môi trường AI trước):</p>
        <pre><code>{aiFolder}{"\n"}{dashboardCommand}</code></pre>
        <p>Thử clip <code>01_FALL_CONFIRMED_URFD_fall-19.mp4</code> trong thư mục video mẫu và bật tùy chọn Video mẫu URFD.</p>
      </details>
      <div className="fall-actions">
        <Button type="button" aria-expanded={showDashboard} onClick={() => setShowDashboard((open) => !open)}>
          {showDashboard ? "Đóng bảng FallGuard" : "Mở bảng FallGuard trong trang"}
        </Button>
        <a className="button button--secondary" href={aiUrl} target="_blank" rel="noopener noreferrer">Mở tab riêng <ExternalLink size={16} aria-hidden="true" /></a>
      </div>
      {showDashboard && <iframe className="fall-iframe" title="Bảng phân tích FallGuard trên máy này" src={aiUrl} sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" referrerPolicy="no-referrer" />}
    </section>
    <p className="fall-note">Đây là công cụ hỗ trợ theo dõi, không thay thế việc kiểm tra trực tiếp khi có nghi ngờ té ngã.</p>
  </>;
}
