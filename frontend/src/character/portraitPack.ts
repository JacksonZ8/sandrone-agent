// 通话形象素材包：每种表情一张母版 + 张嘴贴片，平静表情另有半闭/全闭眼贴片（自备，格式见 docs/character.md）。
export interface PatchInfo {
  src: string;
  x: number;
  y: number;
  w: number;
  h: number;
  cx?: number;
  cy?: number;
}

export interface PortraitManifest {
  version: number;
  size: number;
  emotions: Record<string, { master: string; mouth: PatchInfo }>;
  blink: { half: PatchInfo; closed: PatchInfo };
  framing: Record<"avatar" | "full", { x: number; y: number; w: number; h: number }>;
}

export interface LoadedPack {
  manifest: PortraitManifest;
  base: string;
  images: Map<string, HTMLImageElement>;
  /** 按需加载某张图，加载完成前返回 null */
  image: (name: string) => HTMLImageElement | null;
}

const packs = new Map<string, Promise<LoadedPack>>();

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`形象素材加载失败：${url}`));
    image.src = url;
  });
}

/** 同一素材包只加载一次；先加载平静母版与贴片，其他表情母版在后台继续加载 */
export function loadPortraitPack(manifestUrl: string): Promise<LoadedPack> {
  let pack = packs.get(manifestUrl);
  if (!pack) {
    pack = (async () => {
      const response = await fetch(manifestUrl);
      if (!response.ok) throw new Error(`形象素材清单读取失败（HTTP ${response.status}）`);
      const manifest = (await response.json()) as PortraitManifest;
      const base = manifestUrl.slice(0, manifestUrl.lastIndexOf("/") + 1);
      const images = new Map<string, HTMLImageElement>();
      const pending = new Set<string>();
      const fetchImage = (name: string) => {
        if (images.has(name) || pending.has(name)) return;
        pending.add(name);
        void loadImage(base + name).then(
          (image) => images.set(name, image),
          () => pending.delete(name),
        );
      };
      const calm = manifest.emotions["平静"];
      const first = [
        calm.master,
        calm.mouth.src,
        manifest.blink.half.src,
        manifest.blink.closed.src,
      ];
      await Promise.all(first.map(async (name) => images.set(name, await loadImage(base + name))));
      for (const entry of Object.values(manifest.emotions)) {
        fetchImage(entry.master);
        fetchImage(entry.mouth.src);
      }
      return {
        manifest,
        base,
        images,
        image: (name: string) => {
          fetchImage(name);
          return images.get(name) ?? null;
        },
      };
    })();
    packs.set(manifestUrl, pack);
    pack.catch(() => packs.delete(manifestUrl));
  }
  return pack;
}
