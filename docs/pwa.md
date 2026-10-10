# 手机上使用（PWA + Tailscale）

服务仍跑在这台 Mac 上，手机通过 Tailscale 的私有网络用 HTTPS 访问，再「添加到主屏幕」当成 App 用。全程不暴露到公网。

## 一次性准备

1. Mac 与 iPhone 都安装 Tailscale，并登录**同一个** tailnet 账号。
2. 在 Tailscale 管理后台开启 MagicDNS 与 HTTPS 证书（Serve 需要）。
3. 在项目根 `.env` 里写上允许远程访问的 tailnet 登录名（Tailscale 后台「Users」里看到的那个，多个用逗号分隔）：

   ```
   SANDRONE_TAILSCALE_LOGINS=你的登录名
   ```

   不设置时，所有远程请求都会被拒绝。
4. 构建前端（手机访问的是后端直接提供的构建产物，不是 5173 开发服务器）：

   ```bash
   cd frontend && npm run build
   ```

5. 重启后端，然后在 Mac 上把 8000 端口通过 Tailscale Serve 发布到 tailnet：

   ```bash
   tailscale serve --bg 8000
   ```

   `tailscale serve status` 会显示形如 `https://你的Mac名.xxxx.ts.net` 的地址。

> 不要用 `tailscale funnel`：Funnel 会把服务暴露到公网。

## 在 iPhone 上安装

1. 用 Safari 打开上面的 `https://…ts.net` 地址。
2. 分享 → 「添加到主屏幕」。之后从主屏幕图标打开就是全屏 App，语音通话可以用麦克风（HTTPS 下才允许）。

## 安全边界

- 后端只监听 127.0.0.1，手机只能经 Tailscale Serve 进来。
- Serve 会在请求上附带访问者的 `Tailscale-User-Login`；后端只放行 `SANDRONE_TAILSCALE_LOGINS` 里的账号，Host 必须是 `*.ts.net`，写请求与语音 WebSocket 的 Origin 必须是同一个地址。
- 本机访问（localhost / 127.0.0.1）规则不变。

## 离线与更新

- App 外壳（页面与静态资源）会缓存，断网时还能打开界面；对话、语音和她的记忆都需要连上 Mac。
- 前端重新构建后，下次打开会自动拿到新版本（`sw.js` 不缓存）。

## 推送通知

1. iPhone 需要 iOS 16.4 以上，并且必须从主屏幕图标打开 App（Safari 标签页里不能开推送）。
2. 打开「她的资料」→「手机通知」，允许通知；打开后可以点「试一条」。
3. 之后她主动发消息时：微信可用就照常发微信，同时推送到手机；微信没连上时只推送（消息照常进入时间线）。
4. 推送服务返回订阅失效（404/410）时，后端会自动删除这条订阅，重新打开开关即可。

VAPID 私钥在第一次请求 `/api/push/key` 时生成，保存在 `data/push/vapid_private.pem`（权限 600，不进 Git，不经接口返回）。删掉它会让所有设备的订阅失效，需要在手机上重新打开通知。
