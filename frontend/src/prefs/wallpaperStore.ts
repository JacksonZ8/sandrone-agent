// 自定义壁纸只存在本机 IndexedDB 里，不上传到后端。
const DB_NAME = "sandrone";
const STORE = "wallpaper";
const KEY = "custom";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest,
): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = action(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result as T);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export function readCustomWallpaper(): Promise<Blob | undefined> {
  return run<Blob | undefined>("readonly", (store) => store.get(KEY));
}

export function writeCustomWallpaper(blob: Blob): Promise<unknown> {
  return run("readwrite", (store) => store.put(blob, KEY));
}

export function clearCustomWallpaper(): Promise<unknown> {
  return run("readwrite", (store) => store.delete(KEY));
}

/** 取图片平均亮度，决定沿用浅色还是深色墨色。 */
export async function imageTone(blob: Blob): Promise<"light" | "dark"> {
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = 32;
  canvas.height = 32;
  const context = canvas.getContext("2d");
  if (!context) return "light";
  context.drawImage(bitmap, 0, 0, 32, 32);
  bitmap.close();
  const { data } = context.getImageData(0, 0, 32, 32);
  let total = 0;
  for (let index = 0; index < data.length; index += 4) {
    total += 0.2126 * data[index] + 0.7152 * data[index + 1] + 0.0722 * data[index + 2];
  }
  return total / (data.length / 4) > 140 ? "light" : "dark";
}
