import { useEffect, useState, type CSSProperties } from "react";
import { useLive2DStatus } from "../live2d/status";
import type { PortraitManifest } from "./portraitPack";

const manifests = new Map<string, Promise<PortraitManifest | null>>();

function loadManifest(url: string): Promise<PortraitManifest | null> {
  let manifest = manifests.get(url);
  if (!manifest) {
    manifest = fetch(url)
      .then((response) => (response.ok ? (response.json() as Promise<PortraitManifest>) : null))
      .catch(() => null);
    manifests.set(url, manifest);
  }
  return manifest;
}

/** 平静母版按素材包的头像取景裁成背景：纯 CSS，时间线里很多条消息也不开画布 */
function cropStyle(manifest: PortraitManifest, base: string): CSSProperties {
  const crop = manifest.framing.avatar;
  const size = manifest.size;
  const master = manifest.emotions["平静"]?.master ?? Object.values(manifest.emotions)[0]?.master;
  return {
    backgroundImage: `url("${base}${master}")`,
    backgroundSize: `${(size / crop.w) * 100}%`,
    backgroundPosition: `${(crop.x / (size - crop.w)) * 100}% ${(crop.y / (size - crop.h)) * 100}%`,
  };
}

interface Props {
  className?: string;
}

/** 消息旁的小头像：有逐帧素材包时用平静母版的脸部，没有时显示一枚「桑」字印 */
export default function HerAvatar({ className }: Props) {
  const status = useLive2DStatus();
  const url = status?.portrait_url ?? null;
  const [style, setStyle] = useState<CSSProperties | null>(null);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    void loadManifest(url).then((manifest) => {
      if (cancelled || !manifest) return;
      setStyle(cropStyle(manifest, url.slice(0, url.lastIndexOf("/") + 1)));
    });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return (
    <span className={className} style={style ?? undefined} data-seal={!style} aria-hidden="true">
      {style ? null : "桑"}
    </span>
  );
}
