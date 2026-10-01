"use client";

import { useState } from "react";
import { AudioLines, File, FileX, Film, Image as ImageIcon } from "lucide-react";
import { assetUrl } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { assetKind, tintFor } from "@/lib/studio";
import type { Asset } from "@/lib/types";
import { cn } from "@/lib/utils";

const kindIcon = { video: Film, image: ImageIcon, audio: AudioLines, other: File };

/** Real media thumbnail: first video frame, the image itself, or a tinted tile for audio. */
export function MediaThumb({ asset, className, controls = false }: { asset: Asset; className?: string; controls?: boolean }) {
  const { t } = useI18n();
  // A file removed by retention or by its owner answers 410: say so instead of showing a broken frame.
  const [missing, setMissing] = useState(false);
  const kind = assetKind(asset.content_type);
  const src = assetUrl(asset.id);
  if (missing) {
    return (
      <div className={cn("flex size-full flex-col items-center justify-center gap-1.5 bg-surface-2 p-3 text-center", className)}>
        <FileX className="size-5 text-muted-foreground" />
        <span className="text-[11px] text-muted-foreground">{t.media.unavailable}</span>
      </div>
    );
  }
  if (kind === "image") {
    return (
      <img
        src={src}
        alt={asset.filename}
        loading="lazy"
        onError={() => setMissing(true)}
        className={cn("size-full object-cover", className)}
      />
    );
  }
  if (kind === "video") {
    return (
      <video
        onError={() => setMissing(true)}
        src={controls ? src : `${src}#t=0.5`}
        controls={controls}
        muted={!controls}
        playsInline
        preload="metadata"
        className={cn("size-full bg-black object-contain", className)}
      />
    );
  }
  const Icon = kindIcon[kind];
  return (
    <div className={cn("flex size-full flex-col items-center justify-center gap-3 bg-gradient-to-br p-3", tintFor(asset.id), className)}>
      <Icon className="size-6 text-foreground/80" />
      {controls && kind === "audio" && <audio src={src} controls preload="metadata" className="w-full max-w-xs" />}
    </div>
  );
}

export { kindIcon as assetKindIcon };
