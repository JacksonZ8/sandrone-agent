export function errorText(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

export function splitNames(text: string): string[] {
  return text
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter((item) => item.length >= 2);
}
