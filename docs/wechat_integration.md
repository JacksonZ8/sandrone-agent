# 微信接入设计（私聊 v1）

## 方案

采用 Hermes 当前使用的腾讯 iLink Bot API：

- Base URL：`https://ilinkai.weixin.qq.com`
- 登录：`GET /ilink/bot/get_bot_qrcode?bot_type=3`
- 扫码状态：`GET /ilink/bot/get_qrcode_status?qrcode=...`
- 收消息：`POST /ilink/bot/getupdates`，长轮询
- 发消息：`POST /ilink/bot/sendmessage`

请求头包含：

- `Authorization: Bearer <bot_token>`
- `AuthorizationType: ilink_bot_token`
- `iLink-App-Id: bot`
- `iLink-App-ClientVersion`
- `X-WECHAT-UIN`

消息体带 `base_info.channel_version`，回复需要回传 `context_token`。

## 当前能力

- 微信私聊文字收发。
- 扫码登录与凭据持久化。
- 主人绑定：设置页生成 6 位一次性配对码（10 分钟有效，错误 10 次作废），由你自己的微信发给 Bot 完成绑定；未绑定时任何消息都不进入桑多涅大脑，发送者只收到一次绑定提示。之后只接受 owner 消息，其他人收到一句冷淡拒绝。
- 主人的微信私聊写入与网页相同的 `main` 时间线，她能接着网页里的话题聊；回复时会被提示按微信习惯短句、不用 Markdown。2026-10-08 之前的微信记录仍在旧线程 `wechat:dm:<id>`，未迁移，但可通过长期记忆与情景召回被想起。
- 微信消息进入桑多涅同一个大脑和长期记忆。
- 好感度在微信私聊中照常更新。

## 主动消息

- 后端内调度器每 5 分钟检查一次；每天按设置随机规划 1–N 个时刻（彼此间隔 ≥90 分钟），只规划从当前起的剩余时段，停机错过的时刻不补发。
- 到点后依次检查：安静时段、当日上限、对方最近是否说过话、上一条主动消息是否还没被回复、微信是否已连接并绑定主人；任一不满足即记录 `skipped`。
- 通过后由她基于人设、记忆、日记、时间和最近对话输出 `{"send": bool, "text": ...}`，可以选择不发（`declined`）；发送成功的消息以 `wechat_dm` 渠道写入 `main` 时间线。
- 发送时若会话已过期（errcode -14），去掉 `context_token` 重试一次；这是参考实现 Hermes 用于定时推送的降级路径，尚未在本项目真实账号上验证。

## 暂不支持

- 群聊：iLink bot 身份通常无法进入普通微信群；等确定 WeChatFerry/Windows 方案后再做。
- 语音条：iLink Bot 接口不显示机器人发出的语音条，不支持。
- 图片/文件：协议已预留，当前未开放。

## 风险

- iLink Bot 身份与普通微信个人号不同，不是把现有个人微信直接变成机器人。
- 仍应使用专门账号测试，控制回复频率。
