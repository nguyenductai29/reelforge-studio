"use client";

import { useEffect, useRef, useState } from "react";
import "./portrait-preview.css";

type PreviewAsset = { id: string; filename: string; content_type: string; bytes: number };
type PreviewProps = { asset: PreviewAsset | null; onOpenLibrary: () => void };

function FrameIcon({ kind }: { kind: "frame" | "expand" | "media" | "audio" }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === "frame" && <><rect x="6" y="3" width="12" height="18" rx="1.5" /><path d="M9 7h6M9 17h6" strokeDasharray="1 3" /></>}
    {kind === "expand" && <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />}
    {kind === "media" && <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="8" cy="9" r="1.5" /><path d="m3 17 5-5 4 4 4-6 5 7" /></>}
    {kind === "audio" && <><path d="M9 18V5l11-2v13M9 9l11-2" /><ellipse cx="5.5" cy="18" rx="3.5" ry="3" /><ellipse cx="16.5" cy="16" rx="3.5" ry="3" /></>}
  </svg>;
}

function timeLabel(seconds: number) {
  if (!Number.isFinite(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function PreviewSession({ asset, onOpenLibrary }: PreviewProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const [safeArea, setSafeArea] = useState(false);
  const [cover, setCover] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [muted, setMuted] = useState(false);
  const [playbackError, setPlaybackError] = useState("");
  const [dimensions, setDimensions] = useState("");
  const [duration, setDuration] = useState<number | null>(null);
  const [fullscreenAvailable, setFullscreenAvailable] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState("");
  const type = asset?.content_type.split("/")[0];
  const visual = type === "image" || type === "video";
  const supported = visual || type === "audio";
  const source = asset ? `/api/assets/${encodeURIComponent(asset.id)}` : undefined;
  const stateLabel = !asset ? "CHỜ MEDIA" : failed ? "KHÔNG THỂ TẢI" : !supported ? "CHƯA HỖ TRỢ" : !ready ? "ĐANG TẢI" : playing ? "ĐANG PHÁT" : "SẴN SÀNG";

  useEffect(() => {
    setFullscreenAvailable(Boolean(document.fullscreenEnabled));
    const updateFullscreen = () => setIsFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener("fullscreenchange", updateFullscreen);
    return () => document.removeEventListener("fullscreenchange", updateFullscreen);
  }, []);

  function mediaError() { setFailed(true); setPlaying(false); }
  async function togglePlayback() {
    if (!mediaRef.current) return;
    setPlaybackError("");
    try {
      if (mediaRef.current.paused) await mediaRef.current.play();
      else mediaRef.current.pause();
    } catch { setPlaybackError("Chưa thể phát tệp. Hãy thử lại hoặc chọn media khác."); }
  }
  async function enterFullscreen() {
    setFullscreenError("");
    try {
      if (document.fullscreenElement === stageRef.current) await document.exitFullscreen();
      else await stageRef.current?.requestFullscreen();
    } catch { setFullscreenError("Trình duyệt chưa cho phép mở toàn màn hình."); }
  }

  return <section className="portrait-preview" aria-labelledby="portrait-preview-title">
    <header className="pp-header">
      <div className="pp-heading"><span className="pp-monitor-mark" aria-hidden="true" /><h2 id="portrait-preview-title">Màn hình preview</h2></div>
      <span className="pp-ratio-badge">9:16 <span>PORTRAIT</span></span>
    </header>

    <div className="pp-stage" ref={stageRef}>
      {isFullscreen && <button type="button" className="pp-fullscreen-exit" onClick={() => void enterFullscreen()}>Thu nhỏ <span aria-hidden="true">×</span></button>}
      <div className="pp-stage-top"><span>PREVIEW MONITOR</span><span className={`pp-signal${ready && !failed ? " pp-signal-ready" : ""}`} role="status"><i aria-hidden="true" />{stateLabel}</span></div>
      <div className="pp-canvas"><div className="pp-frame-wrap">
        <span className="pp-axis pp-axis-top" aria-hidden="true">9</span>
        <span className="pp-axis pp-axis-side" aria-hidden="true">16</span>
        <div className={`pp-frame${!asset ? " pp-frame-empty" : ""}${cover ? " pp-frame-cover" : ""}`}>
          {!asset && <div className="pp-empty">
            <span className="pp-empty-top">YOUR NEXT STORY</span>
            <div className="pp-focus-art" aria-hidden="true"><span className="pp-focus-circle" /><svg viewBox="0 0 120 120" fill="none"><path d="M20 40V20h20m40 0h20v20m0 40v20H80m-40 0H20V80" stroke="currentColor" strokeWidth="1.5" /><path d="M60 43v34M43 60h34" stroke="currentColor" strokeWidth="1.5" /><circle cx="60" cy="60" r="5" fill="currentColor" /></svg></div>
            <div className="pp-empty-copy"><h3>Bắt đầu<br />từ một khung hình.</h3><p>Chọn ảnh hoặc video từ kho<br />để xem trước tại đây.</p></div>
            <span className="pp-empty-bottom"><span aria-hidden="true">＋</span> KHÔNG GIAN CHO Ý TƯỞNG CỦA BẠN</span>
          </div>}

          {asset && !failed && type === "image" && <img className="pp-source" src={source} alt={asset.filename} onLoad={event => { setReady(true); setDimensions(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`); }} onError={mediaError} />}
          {asset && !failed && type === "video" && <video ref={node => { mediaRef.current = node; }} className="pp-source" src={source} controls={isFullscreen} playsInline preload="metadata" aria-label={`Xem trước ${asset.filename}`} onLoadedMetadata={event => { setReady(true); setDimensions(`${event.currentTarget.videoWidth} × ${event.currentTarget.videoHeight}`); setDuration(event.currentTarget.duration); }} onError={mediaError} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={event => setPosition(event.currentTarget.currentTime)} onVolumeChange={event => setMuted(event.currentTarget.muted)} onDurationChange={event => setDuration(event.currentTarget.duration)} muted={muted} />}
          {asset && !failed && type === "audio" && <div className="pp-audio"><div className="pp-audio-art" aria-hidden="true"><FrameIcon kind="audio" /></div><span className="pp-audio-label">AUDIO PREVIEW</span><h3>{asset.filename}</h3><p>Tệp âm thanh</p><audio ref={node => { mediaRef.current = node; }} src={source} controls={isFullscreen} preload="metadata" aria-label={`Nghe trước ${asset.filename}`} onLoadedMetadata={event => { setReady(true); setDuration(event.currentTarget.duration); }} onError={mediaError} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={event => setPosition(event.currentTarget.currentTime)} onVolumeChange={event => setMuted(event.currentTarget.muted)} onDurationChange={event => setDuration(event.currentTarget.duration)} muted={muted} /></div>}

          {asset && (failed || !supported) && <div className="pp-unavailable" role={failed ? "alert" : undefined}><FrameIcon kind="media" /><h3>{failed ? "Chưa thể mở media" : "Chưa hỗ trợ xem trước"}</h3><p>{failed ? "Tệp không tải được hoặc định dạng chưa được trình duyệt hỗ trợ." : "Bạn có thể tải tệp về để mở trên thiết bị."}</p></div>}
          {asset && supported && !ready && !failed && <div className="pp-loading" role="status"><span />Đang tải media…</div>}
          {safeArea && <div className="pp-safe-overlay" aria-hidden="true"><div /><span>VÙNG AN TOÀN THAM KHẢO</span></div>}
        </div>
      </div></div>
      <div className="pp-stage-bottom"><span>KHUNG DỌC</span><span>{dimensions || (type === "audio" ? "ÂM THANH" : "9:16")}{duration !== null && ` · ${timeLabel(duration)}`}</span></div>
    </div>

    {asset && (type === "video" || type === "audio") && !failed && <div className="pp-transport" aria-label="Điều khiển phát media"><button type="button" className="pp-control pp-icon-control" aria-label={playing ? "Tạm dừng" : "Phát media"} disabled={!ready} onClick={() => void togglePlayback()}>{playing ? "Ⅱ" : "▶"}</button><span className="pp-time">{timeLabel(position)}</span><input type="range" min={0} max={duration !== null && Number.isFinite(duration) ? duration : 0} step="0.1" value={position} disabled={!ready || !duration || !Number.isFinite(duration)} aria-label="Vị trí phát" onChange={event => { const value = Number(event.target.value); if (mediaRef.current) mediaRef.current.currentTime = value; setPosition(value); }} /><button type="button" className="pp-control pp-mute" aria-label={muted ? "Bật âm thanh" : "Tắt âm thanh"} aria-pressed={muted} onClick={() => setMuted(!muted)}>{muted ? "Bật tiếng" : "Tắt tiếng"}</button></div>}
    <div className="pp-toolbar" aria-label="Điều khiển preview">
      <button type="button" className={safeArea ? "pp-control pp-control-active" : "pp-control"} aria-pressed={safeArea} onClick={() => setSafeArea(!safeArea)} title="Hiện vùng an toàn tham khảo"><FrameIcon kind="frame" /><span>Vùng an toàn</span></button>
      <div className="pp-toolbar-end">
        <button type="button" className={cover ? "pp-control pp-control-active" : "pp-control"} aria-pressed={cover} disabled={!visual || failed} onClick={() => setCover(!cover)} title={cover ? "Hiện toàn bộ media trong khung" : "Phóng media để lấp đầy khung"}>{cover ? "Lấp đầy" : "Vừa khung"}</button>
        {fullscreenAvailable && <button type="button" className="pp-control pp-icon-control" onClick={() => void enterFullscreen()} aria-label="Mở preview toàn màn hình" title="Toàn màn hình"><FrameIcon kind="expand" /></button>}
      </div>
    </div>
    {(fullscreenError || playbackError) && <p className="pp-feedback" role="status">{fullscreenError || playbackError}</p>}
    <footer className="pp-source-info"><span className="pp-source-icon"><FrameIcon kind={type === "audio" ? "audio" : "media"} /></span><div><span className="pp-source-label">NGUỒN MEDIA</span><p title={asset?.filename}>{asset?.filename ?? "Chưa chọn tệp"}</p></div><span className="pp-source-size">{asset ? `${(asset.bytes / 1048576).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB` : "—"}</span>{asset && (failed || !supported) && <a className="pp-control pp-icon-control" href={source} download={asset.filename} aria-label="Tải tệp gốc" title="Tải tệp gốc">↓</a>}<button type="button" className="pp-control pp-library-control" onClick={onOpenLibrary} aria-label={asset ? "Chọn media khác" : "Chọn media"} title="Chọn nguồn media"><FrameIcon kind="media" /><span>{asset ? "Đổi tệp" : "Chọn media"}</span></button></footer>
  </section>;
}

export default function PortraitPreview(props: PreviewProps) {
  return <PreviewSession key={props.asset?.id ?? "empty"} {...props} />;
}
