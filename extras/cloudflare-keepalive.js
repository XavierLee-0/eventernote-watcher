/**
 * Cloudflare Worker: 定时保活 eventernote-watcher (部署在 Koyeb 等会休眠的平台)
 *
 * 部署步骤:
 * 1. dash.cloudflare.com → Workers & Pages → Create → Worker → 粘贴本文件代码
 * 2. 把下面的 APP_URL 换成你的应用地址 (如 https://xxx.koyeb.app)
 * 3. Worker → Settings → Triggers (或 Cron Triggers) → 添加 Cron: */5 * * * *
 *    (每 5 分钟触发一次, 小于 Koyeb 的休眠等待秒数即可)
 * 4. 部署。可在 Worker 的 Logs 里观察每次触发是否成功
 *
 * 原理: Koyeb 免费实例无流量一段时间会休眠, 休眠时应用里的轮询调度器停止工作。
 * Cron 触发器定时访问 /healthz (无需鉴权的探活端点), 保持实例常醒。
 */

const APP_URL = "https://your-app.koyeb.app"; // TODO: 换成你的应用地址

export default {
  async scheduled(event, env, ctx) {
    const resp = await fetch(`${APP_URL}/healthz`, {
      // 给足超时余量; 冷启动可能需要几秒
      signal: AbortSignal.timeout(25000),
    });
    const body = await resp.text();
    console.log(`keepalive -> ${resp.status} ${body}`);
    if (resp.status !== 200) {
      console.error("keepalive failed"); // 日志里可见, 便于排查
    }
  },
};
