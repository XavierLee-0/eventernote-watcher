# Eventernote Watcher

监视 [Eventernote](https://www.eventernote.com/) 上指定出演者的新活动，通过 WxPusher / PushPlus / 邮件推送提醒，并输出 iCal 日历订阅。

提供两种部署形态，**二选一或同时使用均可**（共享同一套核心代码）：

| | WebUI 常驻模式 | GitHub Actions 模式 |
|---|---|---|
| 适合 | 树莓派 / NAS / 云主机等常开设备 | 不想维护任何服务器 |
| 配置方式 | 浏览器打开 WebUI | 改仓库里的 JSON 文件 |
| 费用 | 设备本身的成本 | 免费 |
| 日历订阅 | 本服务 `/api/calendar.ics`（可加 token 保护） | GitHub Pages 公开 URL |
| 抓取频率 | 可调（默认每 8 小时） | 每天 3 次固定 |

> 两种模式同时运行时，同一通知渠道会收到两次推送；只跑一边时把另一边的通知渠道关掉即可。

---

## 目录

- [方式一：WebUI 常驻模式](#方式一webui-常驻模式)
  - [Docker 部署（推荐）](#docker-部署推荐)
  - [首次配置（三步）](#首次配置三步)
  - [不用 Docker 的本地运行](#不用-docker-的本地运行)
- [方式二：GitHub Actions 模式](#方式二github-actions-模式)
- [日历订阅（Google Calendar 等）](#日历订阅google-calendar-等)
- [公网部署：鉴权配置](#公网部署鉴权配置)
- [常见问题](#常见问题)

---

## 方式一：WebUI 常驻模式

### Docker 部署（推荐）

前置要求：设备上已装 Docker（树莓派装 64 位系统即可，镜像支持 arm64）。

```bash
git clone https://github.com/<你的地址>/eventernote-watcher.git
cd eventernote-watcher
docker compose up -d --build
```

浏览器打开 `http://<设备IP>:8000`，看到出演者页面即部署成功。

数据（出演者、活动快照、设置）都存在 `./data/eventernote.db`，随容器升级保留；备份这个文件即可备份全部数据。

局域网使用到此就够了。**如果要暴露到公网，先看[公网部署：鉴权配置](#公网部署鉴权配置)再继续。**

### 首次配置（三步）

1. **配置通知渠道**：「设置」页 → 打开想用的渠道开关（WxPusher / PushPlus / 邮件）→ 填写对应参数 → 保存 → 点「发送测试推送」确认手机能收到
2. **添加出演者**：「出演者」页 → 搜索框输入出演者名（**必须用日文汉字写法**，如 `斉藤朱夏`、`藪島朱音`；简体字搜不到，见[常见问题](#常见问题)）→ 点「添加」
3. **建立基线**：点右上角「立即抓取」。首次抓取只记录当前已有的活动（不发通知），之后轮询中**新出现**的活动才会推送

> 「设置」页底部有**配置导出/导入**：导出的 JSON 包含出演者列表和全部设置（含通知渠道 token），用于备份或迁移部署。导入会替换当前出演者列表；活动快照不随配置走，导入后首次抓取自动重建基线、不会推送轰炸。**导出文件含密钥，请妥善保管。**

各通知渠道参数获取方式：

| 渠道 | 参数 | 哪里获取 |
|---|---|---|
| WxPusher | app_token + uid | [wxpusher.zjiecode.com](https://wxpusher.zjiecode.com/) 注册应用得 app_token；微信扫码关注其公众号后，在「用户」页看到 uid |
| PushPlus | token | [pushplus.plus](https://www.pushplus.plus/) 微信登录后在「一对一推送」页 |
| 邮件 | SMTP 参数 | 以 QQ 邮箱为例：设置 → 账户 → 开启 SMTP → 生成授权码；host `smtp.qq.com`，port `465`，username 是邮箱地址，password 填授权码（不是QQ密码） |

### 不用 Docker 的本地运行

前置要求：Python 3.10+。

```bash
git clone https://github.com/<你的地址>/eventernote-watcher.git
cd eventernote-watcher
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

数据库默认生成在项目目录 `data/` 下。公网部署时鉴权用环境变量传入：

```bash
AUTH_PASSWORD=你的密码 ICS_TOKEN=随机串 uvicorn app.main:app --port 8000
```

---

## 方式二：GitHub Actions 模式

不需要任何服务器，定时抓取由 GitHub Actions 完成，日历文件发布在 GitHub Pages。整个过程约 10 分钟。

前置要求：一个 GitHub 账号；出演者的 actor_id（获取方式见第 3 步）。

**第 1 步：创建私有仓库并推送代码**

> 必须是**私有**仓库：抓取状态数据库和日历文件会提交进仓库，公开仓库等于把你的日程公开。

```bash
git clone https://github.com/<你的地址>/eventernote-watcher.git   # 或下载 zip 解压
cd eventernote-watcher
rm -rf data site            # 清掉本地的测试数据
git init && git add -A && git commit -m "init"
# 在 github.com 上新建一个 Private 仓库后:
git remote add origin git@github.com:<你的用户名>/<仓库名>.git
git push -u origin main
```

**第 2 步：配置出演者**

编辑仓库根目录的 `actors.json`，格式如下（actor_id 和 name 见第 3 步）：

```json
[
  {"actor_id": 15436, "name": "斉藤朱夏", "enabled": true},
  {"actor_id": 62052, "name": "薮島朱音", "enabled": true}
]
```

**第 3 步：拿到出演者的 actor_id**

打开出演者的 Eventernote 页面，URL 最后一段数字就是：`https://www.eventernote.com/actors/斉藤朱夏/15436` → actor_id 为 `15436`。不知道页面地址的话，先在 [eventernote.com](https://www.eventernote.com/) 顶部搜索框搜出演者（日文写法）。

**第 4 步：配置通知密钥**

仓库页面 → Settings → Secrets and variables → Actions → New repository secret，按需添加（没配置的渠道自动禁用）：

| Secret 名 | 对应渠道 | 是否必须 | 内容 |
|:--|:--|:--|:--|
| `WXPUSHER_APP_TOKEN` + `WXPUSHER_UID` | WxPusher | 可选 | app_token / uid（获取方式同上表） |
| `PUSHPLUS_TOKEN` | PushPlus | 可选 | token |
| `EMAIL_HOST` / `EMAIL_USERNAME` / `EMAIL_PASSWORD` / `EMAIL_FROM` / `EMAIL_TO` | 邮件 | 可选 | SMTP 参数，获取方式同上表 |

**第 5 步：开启 GitHub Pages**

仓库页面 → Settings → Pages → Source 选 `Deploy from a branch`，分支 `main`、目录 `/ (root)` → Save。等几分钟生效。

**第 6 步：首次运行**

仓库页面 → Actions 标签 → 左侧选 `watch` workflow → 右侧 `Run workflow` 按钮手动触发一次。

运行完成后会看到一次 bot 的 commit（提交了数据库和 `site/calendar.ics`），这就是正常状态。日历订阅链接为：

```
https://<你的用户名>.github.io/<仓库名>/calendar.ics
```

之后每天自动运行 3 次（北京时间 01:07 / 09:07 / 17:07，Actions 定时任务可能有几十分钟延迟，属正常）。增删出演者 = 改 `actors.json` 后 commit，无需其他操作。

> 限制：此模式下没有 WebUI；日历 URL 是公开的（知道链接的人都能看到你的演出日程，不含其他个人信息）；私有仓库每月 2000 Actions 分钟免费额度，本项目每次运行约 1 分钟。

---

## 日历订阅（Google Calendar 等）

活动数据可输出为 iCal 订阅源，自动同步到日历应用（Google / Apple 日历等，刷新周期由客户端决定，一般几小时一次）。

- **WebUI 模式**：「活动」页顶部有「复制订阅链接」按钮。链接形如 `http://<设备IP>:8000/api/calendar.ics`（设置了 ICS_TOKEN 时带 `?token=xxx`）
- **Actions 模式**：即上文第 5 步的 Pages 链接
- Google Calendar 操作：左侧栏「其他日历」→ 「通过网址添加」→ 粘贴链接 → 添加
- 注意：Google 的服务器必须能访问到该 URL——**局域网设备的链接 Google 拉不到**，要么走内网穿透，要么改用 Apple 日历等本机应用订阅

日历中同一活动只出现一条（多位监视中的出演者共同出演时自动去重，出演者名单写在日程描述里）；默认只含未来活动，加 `?all=true` 包含历史。

**演出当天会提醒吗？** `.ics` 已内置提醒（演出前一天 14:00，Apple 日历等客户端直接生效）；但 **Google Calendar 对订阅日历会忽略 `.ics` 里的提醒设置**，需要手动设置一次：Google Calendar → 左栏该日历 → 「设置」→ 「活动通知」，添加默认通知（如提前 1 天）。此设置对该日历所有日程生效，包括以后同步进来的新活动。

---

## 公网部署：鉴权配置

局域网使用无需任何配置。**一旦部署到公网（云主机、Koyeb 等），必须设置鉴权**，否则任何人都能看到你的配置并修改：

在部署环境设置两个环境变量（都不设 = 无鉴权）：

| 环境变量 | 作用 | 说明 |
|---|---|---|
| `AUTH_PASSWORD` | WebUI 与全部 API 的访问密码（Basic Auth） | 浏览器首次访问时弹出登录框 |
| `ICS_TOKEN` | 日历订阅链接的访问 token | 订阅链接变为 `/api/calendar.ics?token=<该值>`，无 token 返回 401 |

随机串生成方式举例：`python -c "import secrets; print(secrets.token_hex(16))"`

Docker compose 下建议在同目录放 `.env` 文件（已被 gitignore）：

```env
AUTH_PASSWORD=你的密码
ICS_TOKEN=随机串
```

然后 `docker compose up -d` 即可生效。

### 部署到 Koyeb（WebUI 模式公网示例）

前置要求：GitHub 账号（代码先推上去）、[koyeb.com](https://www.koyeb.com) 注册。

> **部署前先确认两件事：**
> 1. **免费额度**：Koyeb 的免费政策变动较频繁（平台 2026 年被 Mistral 收购后定价有调整），注册时以官网实际显示的免费档为准。如果只剩付费档，建议换用有持久磁盘的平台（如 Fly.io、Oracle Cloud 免费层）或改用 GitHub Actions 模式
> 2. **没有持久磁盘**：SQLite 数据库存在容器内，**每次重新部署都会清空**——出演者列表、通知配置需要重新设置（重新添加出演者后首次抓取只建基线，不会引发推送轰炸，所以功能不受影响，只是麻烦）。Koyeb 的持久卷（Volumes）是付费功能

**步骤**：

1. Koyeb 控制台 → Create App → GitHub → 选中本项目的仓库，构建方式选 Docker（自动识别仓库里的 Dockerfile）
2. 实例规格选最小的（本项目内存占用 < 150MB）
3. 在 Environment variables 中添加：`AUTH_PASSWORD` = 你的密码、`ICS_TOKEN` = 随机串（生成方式见上文）
4. Exposed port 设为 `8000`（协议 HTTP）
5. Deploy，完成后访问 `https://<应用名>.<组织名>.koyeb.app`，浏览器应弹出 Basic Auth 登录框（**用户名随意填**，如 `user`；密码为 `AUTH_PASSWORD` 的值）

验证鉴权生效：`curl -i https://<应用地址>/api/status` 应返回 401；日历订阅 `https://<应用地址>/api/calendar.ics?token=<随机串>` 应返回日历内容。

之后每次 push 代码到 GitHub，Koyeb 会自动重新部署（数据随之重置，需要重新配置出演者和通知渠道——这是没有持久磁盘的代价）。

**防止休眠**：Koyeb 免费实例在无流量一段时间后会休眠，而本项目的轮询调度器跑在应用进程里，实例休眠 = 停止抓取和推送。解决方法是用定时访问探活端点保持实例常醒，任选其一：

- **Cloudflare Workers**（推荐，见 `extras/cloudflare-keepalive.js`）：创建 Worker 粘贴代码、改掉其中的应用地址，再添加 Cron 触发器 `*/5 * * * *`（每 5 分钟）。免费额度每天 10 万次请求，每 5 分钟仅用 288 次
- **cron-job.org / UptimeRobot**：创建每 5 分钟一次的 HTTP 任务，URL 填 `https://<应用地址>/healthz`

探活端点 `/healthz` 无需密码、只返回 ok，专为保活设计，其余接口仍受 Basic Auth 保护。

---

## 常见问题

**搜索不到出演者？**
搜索按日文字形精确匹配，简体中文写法（齐、岛、薮…）搜不到。请使用日文写法：`斉藤朱夏`、`藪島朱音`。不确定写法时先在 Eventernote 网站上搜（它的搜索框有自动补全，选中的条目就是正确写法）。

**为什么添加后没有收到推送？**
首次抓取只建立基线（把已有活动记为"已见"），不推送；之后新出现的活动才推送。这是防止你被几十条存量活动轰炸的设计。

**微信收不到测试推送？**
WxPusher：确认已扫码关注它的公众号；检查 app_token/uid 是否复制完整。PushPlus：登录官网确认 token 有效、公众号未取关。通知渠道的失败原因会记录在「日志」页。

**抓取失败了（出演者页显示 ⚠）？**
多半是网络问题或站点改版。看「出演者」页的错误信息；若 HTML 结构变了需要更新 `app/fetcher.py`（本项目依赖站点的公开页面结构）。

**树莓派上 `docker compose up --build` 构建慢？**
可以在任意有 Docker 的机器上构建多架构镜像推到 registry，树莓派只 pull：

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <registry>/eventernote-watcher:latest --push .
# docker-compose.yml 中把 build: . 换成 image: <registry>/eventernote-watcher:latest
```

---

## 代码结构（开发者参考）

```
app/
├── main.py           # FastAPI 入口 + 调度生命周期 + 鉴权中间件挂载
├── auth.py           # Basic Auth + .ics token 鉴权
├── api.py            # REST 路由
├── watcher.py        # 轮询循环、新活动检测、通知分发
├── fetcher.py        # Eventernote 页面抓取与解析（纯函数）
├── calendar.py       # iCal 生成
├── db.py             # SQLite 数据层
├── notifiers/        # 通知渠道（base 定义接口，新渠道加一个文件即可）
└── web/index.html    # WebUI（Vue3 CDN，无构建）
scripts/run_once.py   # GitHub Actions 模式入口
actors.json           # Actions 模式的出演者配置
settings.json         # Actions 模式的非密钥设置
```
