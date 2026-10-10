from __future__ import annotations

from collections.abc import Iterable

# 人设里的“道具”意象：出现过多会让她像在背设定。键是展示名，值是命中关键词。
MOTIFS: dict[str, tuple[str, ...]] = {
    "茶": ("茶",),
    "咖啡": ("咖啡",),
    "点心": ("点心", "甜点"),
    "工房": ("工房",),
    "算式与草稿": ("算式", "草稿本"),
    "机械与发条": ("机械", "发条", "机关"),
    "法洁欧": ("法洁欧",),
    "熬夜": ("熬夜",),
    "花草": ("花草",),
    "小动物": ("小动物", "毛茸茸"),
}
CATCHPHRASES = ("荒谬可笑", "演算开始", "很遗憾，但这就是结局", "析构万理")

RECENT_REPLY_WINDOW = 12
MOTIF_REPEAT_THRESHOLD = 2


def recent_repetition_block(recent_replies: Iterable[str], user_text: str = "") -> str:
    """根据她最近的回复生成“别再重复”的提示；对方本轮主动提起的意象不拦。"""
    replies = [text or "" for text in recent_replies][-RECENT_REPLY_WINDOW:]
    if not replies:
        return ""
    user_text = user_text or ""

    repeated: list[str] = []
    for name, keywords in MOTIFS.items():
        if any(keyword in user_text for keyword in keywords):
            continue
        count = sum(1 for reply in replies if any(keyword in reply for keyword in keywords))
        if count >= MOTIF_REPEAT_THRESHOLD:
            repeated.append(f"{name}（{count} 次）")

    used_phrases = [
        phrase for phrase in CATCHPHRASES
        if phrase not in user_text and any(phrase in reply for reply in replies)
    ]

    if not repeated and not used_phrases:
        return ""
    lines = ["## 最近说过的话（避免重复）"]
    if repeated:
        lines.append(f"- 你最近几次回复里已经反复提到：{'、'.join(repeated)}。接下来几轮不要再提，除非对方主动说起。")
    if used_phrases:
        quoted = "、".join(f"「{phrase}」" for phrase in used_phrases)
        lines.append(f"- 你最近已经用过口头禅 {quoted}，这几轮换种说法。")
    return "\n".join(lines)
