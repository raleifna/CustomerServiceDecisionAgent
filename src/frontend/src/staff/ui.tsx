import { X } from "lucide-react";
import { useEffect } from "react";

import { decisionLabels, requestStatusLabels, requestStatusTone } from "./api";

export function StatusPill({ status, decision, small }: { status: string; decision?: string | null; small?: boolean }) {
  // A closed request reads better as its outcome than as "已关闭".
  const label = status === "Closed" && decision ? decisionLabels[decision] ?? decision : requestStatusLabels[status] ?? status;
  const tone = status === "Closed" && (decision === "Approve" || decision === "PartialApprove") ? "green" : requestStatusTone[status] ?? "gray";
  return <span className={`pill pill--${tone} ${small ? "pill--sm" : ""}`}>{label}</span>;
}

export function ImageLightbox({ src, title, onClose }: { src: string; title: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <header><span>{title}</span><button className="icon-btn" onClick={onClose} aria-label="关闭"><X size={20} /></button></header>
      <img src={src} alt={title} onClick={(event) => event.stopPropagation()} />
      <a href={src} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>在新窗口打开原图</a>
    </div>
  );
}
