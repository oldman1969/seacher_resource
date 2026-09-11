# seacher_resource 云服务器部署教程
> 环境：腾讯云 Ubuntu 24.04 + 腾讯云域名 + Cloudflare Tunnel（免备案）。  
> 目标：把「全网资源聚合搜索」部署到公网 HTTPS `https://seacher.renzhengfeng.top`，复用已有 cloudflared 隧道。
>
> 本教程已脱敏（隧道 ID 用 `<你的隧道ID>` 占位）。真实隧道 ID 见本地 open-connector 部署档案（`refer/`，已 gitignore）。

---

## 目录
1. [部署拓扑](#部署拓扑)
2. [核心结论：复用已有隧道，只需三步](#核心结论复用已有隧道只需三步)
3. [前置条件](#前置条件)
4. [第 1 步：拉源码 + 写配置](#第-1-步拉源码--写配置)
5. [第 2 步：构建并启动](#第-2-步构建并启动)
6. [第 3 步：本地验证](#第-3-步本地验证)
7. [第 4 步：cloudflared 加 ingress + DNS](#第-4-步cloudflared-加-ingress--dns)
8. [第 5 步：公网验证](#第-5-步公网验证)
9. [更新已部署服务（升级）](#更新已部署服务升级)
10. [日常运维](#日常运维)
11. [安全说明](#安全说明)
12. [常见问题](#常见问题)
13. [速查表](#速查表)
14. [附录：从零部署（新服务器）](#附录从零部署新服务器)

---

## 部署拓扑
```plain
浏览器 / 任意设备 ──HTTPS──┐
                          ├──> seacher.renzhengfeng.top
                          │         │
                          │         ▼
                          │  Cloudflare 边缘 (HTTPS)
                          │         │ 隧道（出站长连接，复用已有）
                          │         ▼
                          │  云服务器 cloudflared (systemd，已在跑)
                          │         │ 按 hostname 分流
                          │         ▼
                          │  localhost:8000
                          │         ▼
                          │  seacher_resource Docker (FastAPI, 127.0.0.1:8000)
                          │         │ 容器内网 http://pansou:8888
                          │         ▼
                          │  PanSou sidecar Docker (ghcr.io/fish2018/pansou, 127.0.0.1:8888)
                          │         │ 出站
                          ▼         ▼
                 B站 / 网易云 / 豆瓣 / 网盘插件源 / 磁力源 …
```

**访问地址**：`https://seacher.renzhengfeng.top`

**端口规划**（沿用现有约定，不撞已有服务）：

| 服务 | 子域名 | 宿主机端口 | 容器端口 | 目录 |
| --- | --- | --- | --- | --- |
| open-connector（已有） | openconnector.renzhengfeng.top | 3001 | 3000 | ~/open-connector |
| new-api（已有） | newapi.renzhengfeng.top | 3002 | 3000 | ~/new-api |
| **seacher_resource（本次）** | **seacher.renzhengfeng.top** | **8000** | **8000** | **~/seacher_resource** |
| **PanSou sidecar（本次）** | （不对外，仅 app 内部调用） | 8888 | 8888 | 同一 compose |

---

## 核心结论：复用已有隧道，只需三步
open-connector 那套已经把 **Cloudflare 账号、域名、cloudflared、隧道、systemd** 都搭好了——这些是一次性投入，本项目**全部复用**。要做的事只有三处（和 open-connector 教程「加第二个服务」一节同思路）：

1. **Docker 起本项目**（`~/seacher_resource`，绑 `127.0.0.1:8000`）
2. **`~/.cloudflared/config.yml` 加一条 ingress**（`seacher.renzhengfeng.top → localhost:8000`）
3. **加一条 DNS**（`cloudflared tunnel route dns`）

下面按这五步走完即可。

---

## 前置条件
1. 云服务器可 SSH（`ubuntu` 用户），已装 **Docker + Compose v2**（`docker compose version` 有输出）。
2. cloudflared 隧道已跑通（systemd `active (running)`），现有 `config.yml` 里已挂 openconnector/newapi 两个 hostname。
3. `renzhengfeng.top` 的 DNS 已托管在 Cloudflare（状态 Active）。

> 若这是**全新服务器**（没装 Docker / cloudflared），先看文末[附录](#附录从零部署新服务器)。

---

## 第 1 步：拉源码 + 写配置
SSH 上服务器，拉取源码：

```bash
cd ~
git clone https://github.com/oldman1969/seacher_resource.git
cd seacher_resource
```

### 1.1 写 `config.yaml`
容器内 `/app/config.yaml` 由 compose 绑定挂载。**必须把 pansou 的 `base_url` 改成 compose 服务名 `http://pansou:8888`**（仓库示例里的 `http://127.0.0.1:8888` 在容器里指向 app 自己，是错的）：

```bash
cat > config.yaml <<'EOF'
# 全网资源聚合搜索 配置（服务器版）
# 改完无需重启：每次搜索前热加载

# 可选：启用国际源（Internet Archive / Open Library 等）时配代理
# proxy: http://127.0.0.1:7890

providers:
  bilibili: { enabled: true }                       # B 站视频（游客态 wbi）
  netease: { enabled: true }                        # 网易云音乐
  douban: { enabled: true }                         # 豆瓣 suggest（限流敏感）
  pansou:                                          # 网盘/磁力聚合 sidecar
    enabled: true
    base_url: http://pansou:8888                   # 关键：compose 服务名，容器间互通
  # ---- 国际源：on-demand，前端「国际源」勾选才查；服务器无代理，保持不勾选 ----
  internet_archive: { enabled: false }
  openlibrary: { enabled: false }
  gutenberg: { enabled: false }
  librivox: { enabled: false }
  # ---- 知乎/微信：on-demand，前端「知乎/微信」勾选才查（无需在此配置）----
  # 知乎读 .env 的 ZHIHU_COOKIE；微信走搜狗，免配置
  zhihu: { enabled: false }
  wechat: { enabled: false }
  ximalaya: { enabled: false }
  youtube: { enabled: false }                       # 需 YOUTUBE_API_KEY + 代理
  spotify: { enabled: false }                       # 需 SPOTIFY_* + 代理
  podcastindex: { enabled: false }                  # 需 PODCASTINDEX_*
  weread: { enabled: false }

probe:
  per_domain_concurrency: 2
  global_concurrency: 10
  baidu_min_interval: 2.0
  timeout_connect: 3.0
  timeout_read: 8.0
  cache_ttl: 3600
  auto_probe_top: 3

search:
  deadline: 8.0
  per_source_limit: 100
  enrich_top: 10
EOF
```

> **关于国际源**：`internet_archive` / `openlibrary` 是 on-demand 源，由前端「国际源」勾选控制，这里的 `enabled` 不用管。服务器没有代理，勾选后也会超时，所以**保持不勾选**；本地开发才配 `proxy:` 并勾选。

### 1.2 写 `.env`（可空，但文件必须存在）
compose 把它绑定挂载到 `/app/.env`。若宿主上这个文件不存在，Docker 会误创建成**目录**导致启动异常。先创建：

```bash
cat > .env <<'EOF'
# 站点访问密码：留空则无密码；设置后访问先过密码页（仅密码，无用户名）
ACCESS_PASSWORD=换成你的密码

# 管理密码（右上角 ⚙ 设置面板用，与站点访问密码相互独立）：留空则设置面板锁定
ADMIN_PASSWORD=换成你的管理密码

# 知乎搜索登录态：在你自己的电脑浏览器登录 zhihu.com → F12 → Application → Cookies → 复制 z_c0 的值
# 注意：cookie 换 IP 使用可能被知乎风控失效；失效时在你电脑重新登录知乎复制新值，
# 通过网站右上角 ⚙ 设置面板粘贴更新（无需 SSH 上服务器，服务器也无需登录知乎）
ZHIHU_COOKIE=

# 敏感配置（可选）
BILI_SESSDATA=
YOUTUBE_API_KEY=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
PODCASTINDEX_API_KEY=
PODCASTINDEX_API_SECRET=
EOF
```

### 1.3 写 `docker-compose.override.yml`
关键：**绑 `127.0.0.1`**，不暴露公网端口；同时给 `app` 加 `restart: unless-stopped`。用 `!override` 标签**替换**（而非追加）基础 compose 里的 `ports`，避免 `8000:8000`（0.0.0.0）与 `127.0.0.1:8000` 冲突：

```bash
cat > docker-compose.override.yml <<'EOF'
services:
  app:
    restart: unless-stopped
    ports: !override
      - "127.0.0.1:8000:8000"

  pansou:
    ports: !override
      - "127.0.0.1:8888:8888"   # 仅本地调试用；app 走容器内网 http://pansou:8888，不依赖此映射
EOF
```

> 若你服务器的 compose 版本报 `!override` 不识别，直接编辑 `docker-compose.yml` 把两处 `ports` 改成 `"127.0.0.1:8000:8000"` / `"127.0.0.1:8888:8888"` 即可。
>
> `pansou` 的宿主机端口映射其实**不是必须的**——app 是通过容器内网 DNS 名 `pansou` 访问它，不经过宿主机。留着 8888 只是为了本地 `curl` 调试方便。
>
> `.env` / `config.yaml` 的挂载**继承基础 compose 的 volumes**，本 override 不用管：`.env` 已是**可写**（`./.env:/app/.env`，无 `:ro`）——设置面板保存知乎 cookie 需要写入 `.env`；`config.yaml` 保持只读（只需读、热加载）。若你用的是旧版基础 compose（`.env` 带 `:ro`），需先 `git pull` 或手动去掉 `:ro`，否则设置面板保存 cookie 会 500。

---

## 第 2 步：构建并启动
本项目**从源码构建**（`build: .`），首次会拉 `python:3.12-slim` 基础镜像 + `pip install`，需要几分钟：

```bash
docker compose up -d --build
```

国内拉 `ghcr.io/fish2018/pansou:latest` 偶尔慢。若卡住，Ctrl+C 后参考 open-connector 教程配 `registry-mirrors`（只加速 Docker Hub，对 ghcr 不一定有效）；PanSou 镜像很小（Go 单二进制），多等会儿一般能成。实在拉不动 ghcr 时，可用 `docker pull` 换镜像代理源（如 `ghcr.nju.edu.cn` 等，视当时可用性）。

---

## 第 3 步：本地验证
> 若 `.env` 里设了 `ACCESS_PASSWORD`，先用 `/auth` 登录拿到 cookie，后续请求带上（`-b`）。
>
> ```bash
> # 容器状态：app 与 pansou 都 Up
> docker compose ps
> 
> # 登录并保存 cookie（无密码时可跳过）
> curl -s -c /tmp/seacher.cookies -X POST http://localhost:8000/auth \
>   -H "Content-Type: application/json" -d '{"password":"你的密码"}'
> 
> # 数据源状态
> curl -s -b /tmp/seacher.cookies http://localhost:8000/api/providers
> 
> # 聚合搜索（中文需 URL 编码，用 -G --data-urlencode 避免 uvicorn 报 Invalid HTTP request）
> curl -s -b /tmp/seacher.cookies -G "http://localhost:8000/api/search" \
>   --data-urlencode "q=三体" \
>   --data-urlencode "types=video,book,audio,netdisk_share,magnet"
> 
> # 前端页面（期望 200；无密码时去掉 -b）
> curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/seacher.cookies http://localhost:8000
> 
> # PanSou 直连（本地调试，可选；PanSou 不设密码）
> curl -s -X POST http://127.0.0.1:8888/api/search -H "Content-Type: application/json" -d '{"kw":"三体"}'
> ```

期望：
- `/api/providers` 返回 `providers` 数组，`active` 里含 `bilibili / netease / douban / pansou`
- `/api/search` 返回 `{"keyword":"三体","results":{...},"errors":[...],"elapsed_ms":...}`；`pansou` 若首搜 0 条属正常（异步冷启动），隔几秒再搜一次

> 若 `errors` 里 pansou 报 `HTTP xxx` 或连接失败：`docker compose logs pansou` 看 sidecar 是否起来；确认 `config.yaml` 的 `base_url` 是 `http://pansou:8888` 而非 `127.0.0.1:8888`。

---

## 第 4 步：cloudflared 加 ingress + DNS

### 4.1 编辑 `~/.cloudflared/config.yml`
在**兜底规则 `http_status:404` 之前**加一条 `seacher` 规则。覆盖写入最省事（下面为你**当前完整**配置，含已有的 openconnector / newapi）：

```bash
cat > ~/.cloudflared/config.yml <<EOF
tunnel: <你的隧道ID>
credentials-file: /home/ubuntu/.cloudflared/<你的隧道ID>.json

ingress:
  - hostname: openconnector.renzhengfeng.top
    service: http://localhost:3001
  - hostname: newapi.renzhengfeng.top
    service: http://localhost:3002
  - hostname: seacher.renzhengfeng.top
    service: http://localhost:8000
  - service: http_status:404
EOF
```

> **规则顺序**：从上往下匹配，第一个命中的 hostname 生效；`http_status:404` 必须放最后兜底。
>
> 上面 `tunnel` / `credentials-file` 是你现有隧道的真实值，照抄即可；若你的实际 ID 不同，以 `~/.cloudflared/` 下文件名为准。

### 4.2 加 DNS（Cloudflare 自动加 CNAME）
```bash
cloudflared tunnel route dns open-connector seacher.renzhengfeng.top
```

> 若报 `An A, AAAA, or CNAME record with that host already exists`：该子域名在 Cloudflare 已有记录，去 DNS 面板删掉旧记录再重跑。

### 4.3 拷到 systemd 目录并重启
```bash
sudo cp ~/.cloudflared/config.yml /etc/cloudflared/
sudo systemctl restart cloudflared
sudo systemctl status cloudflared   # 期望 active (running)
```

---

## 第 5 步：公网验证
```bash
# 登录拿 cookie
curl -s -c /tmp/seacher.cookies -X POST https://seacher.renzhengfeng.top/auth \
  -H "Content-Type: application/json" -d '{"password":"你的密码"}'

# 聚合搜索（带 cookie；中文用 --data-urlencode 编码）
curl -s -b /tmp/seacher.cookies -G "https://seacher.renzhengfeng.top/api/search" \
  --data-urlencode "q=三体"

# 数据源状态
curl -s -b /tmp/seacher.cookies https://seacher.renzhengfeng.top/api/providers

# 前端（期望 200）
curl -s -o /dev/null -w "%{http_code}\n" -b /tmp/seacher.cookies https://seacher.renzhengfeng.top

# 未登录访问 API 应返回 401
curl -s -o /dev/null -w "%{http_code}\n" "https://seacher.renzhengfeng.top/api/search?q=test"
```

浏览器打开 `https://seacher.renzhengfeng.top`：先进入只有密码框的登录页，输入 `ACCESS_PASSWORD` 后进入，搜「三体」能出分组结果即成功。

---

## 更新已部署服务（升级）

本地代码更新后，重新部署到服务器的完整流程（三步）：

### 1. 本地提交推送

```bash
git add -A
git commit -m "描述本次改动"
git push
```

### 2. 服务器拉取 + 重建

```bash
cd ~/seacher_resource
git pull                       # 拉最新代码
docker compose up -d --build   # 重新构建 app 镜像（代码变了）
docker compose ps              # 确认 app / pansou 都 Up
```

### 3. 补新增的 `.env` 配置（仅当新功能引入新配置项时）

对比 `.env.example`，把新增的键补到服务器 `~/seacher_resource/.env`，然后：

```bash
docker compose restart
```

例如本次升级新增了 `ADMIN_PASSWORD`（管理密码）和 `ZHIHU_COOKIE`（知乎 cookie）。

> **注意**：
> - `config.yaml` / `.env` 是 gitignore，`git pull` **不会覆盖**它们，服务器本地配置保留。
> - 知乎 cookie 换 IP 可能失效：若升级后知乎报 401/403，在你自己的电脑浏览器重新登录知乎复制新的 `z_c0`，通过网站右上角 ⚙ 设置面板粘贴更新（无需 SSH、无需服务器登录知乎）。
> - 只有代码变了才需 `--build` 重建镜像；只改 `config.yaml` / `.env` 用 `docker compose restart` 即可（config.yaml 甚至无需重启，每次搜索热加载）。

---

## 日常运维
```bash
cd ~/seacher_resource

# 状态 / 日志
docker compose ps
docker compose logs -f --tail 100 app
docker compose logs -f --tail 100 pansou

# 改 config.yaml 后无需重启（每次搜索前热加载）；
# 改 docker-compose*.yml 后需重建
docker compose up -d --build

# 重启 / 停 / 升级（升级源码后重建镜像）
docker compose restart
docker compose down                 # 停容器（本服务无持久数据卷，可安全 down）
git pull && docker compose up -d --build

# Cloudflare Tunnel（改了 config.yml 后）
sudo cp ~/.cloudflared/config.yml /etc/cloudflared/
sudo systemctl restart cloudflared
sudo journalctl -u cloudflared -f
```

---

## 安全说明
本项目有两个**相互独立**的密码（都在 `.env`）：

| 密码 | 控制什么 | 留空时 |
|---|---|---|
| `ACCESS_PASSWORD` | 谁能**访问站点**（搜索功能） | 站点开放浏览（无鉴权） |
| `ADMIN_PASSWORD` | 谁能**改配置**（右上角 ⚙ 设置面板，如更新知乎 cookie） | 设置面板锁定 |

**站点访问密码**：设 `ACCESS_PASSWORD=你的密码` 后，访问站点会先进入只有密码框的登录页，输入正确密码后种 HttpOnly 签名 cookie（默认 30 天），之后页面与 `/api/*`、`/docs` 都凭该 cookie 访问。

- 密码只用于本地比对 + 给 cookie 签名，不存 cookie、不落盘；公网段走 Cloudflare 边缘 HTTPS 加密。
- **改密码**：编辑 `~/seacher_resource/.env` 的 `ACCESS_PASSWORD`，然后 `docker compose restart`（改密码会使旧 cookie 签名失效，需重新登录）。
- **退出登录**：`POST /auth/logout` 清 cookie（前端暂未做退出按钮，可手动 curl 或清浏览器 cookie）。
- 想更严格（多用户 / 邮箱登录 / 二步验证），可再叠加 **Cloudflare Access**（Zero Trust → Access → Application，把 `seacher.renzhengfeng.top` 套一层），与本密码页互不冲突。

**管理密码**：设 `ADMIN_PASSWORD=你的密码` 后，点右上角 ⚙ 需先验证管理密码（种 2 小时 admin cookie），通过后才能改配置（当前仅「知乎 cookie」一项，未来可扩展）。管理 cookie **只放行 `/api/admin/*`**，不能访问站点内容——权限不放大。留空则设置面板显示「管理功能未启用」。

> 注意本项目 `.env` 里的 B 站 SESSDATA / 知乎 z_c0 / YouTube / Spotify 等**属于你的私有凭证**，填写后即意味着「能通过密码认证的人都能通过这个搜索服务间接使用它们」。非必要保持留空。

---

## 常见问题
- **pansou 报连接失败 / `base_url` 未配置**：`config.yaml` 里 pansou 的 `base_url` 必须是 `http://pansou:8888`（compose 服务名），不要写 `127.0.0.1`。
- **首搜网盘 0 条**：PanSou 异步模式（4s 快速响应 + 后台补全缓存），隔几秒再搜同一关键词即有结果。
- **网盘结果全是插件源、没有 TG 频道源**：TG 需代理，腾讯云直连被墙。有 socks5 代理时给 pansou 加 `PROXY=socks5://host.containers.internal:7890` 并换官方完整 TG 频道清单。
- **豆瓣/百度频被风控**：config.yaml 已设 `baidu_min_interval: 2.0`、每域名并发 2；不要调太高并发。
- **知乎报「无法获取 d_c0 / 401 / 403」**：知乎对服务器 IP 有风控，且 cookie 换 IP 可能失效。确认 `.env` 的 `ZHIHU_COOKIE` 是最新复制的 `z_c0`；若持续 403 说明 IP 被短期风控，等几小时解封，期间可只测微信/网盘。
- **微信报「搜狗冷却中」**：搜狗反爬，翻页触发验证码后进入 10 分钟冷却，期间自动降级；稍后自动恢复。翻页节流已做随机 2~4s。
- **改 config.yaml 不生效**：确认改的是服务器上的 `~/seacher_resource/config.yaml`（挂载进容器的那个），且没有语法错误（每次搜索热加载，失败会落到默认/上次值）。

---

## 速查表
| 项 | 值 |
| --- | --- |
| 服务器 | 腾讯云 Ubuntu 24.04（`ubuntu` 用户） |
| 域名 | seacher.renzhengfeng.top |
| 项目目录 | ~/seacher_resource |
| app 宿主机端口 | 8000（容器内 8000） |
| PanSou 宿主机端口 | 8888（容器内 8888，仅调试） |
| app → pansou | http://pansou:8888（容器内网） |
| 复用隧道 ID | <你的隧道ID> |
| 鉴权 | 站点密码 `ACCESS_PASSWORD`（留空则开放浏览）+ 管理密码 `ADMIN_PASSWORD`（留空则设置面板锁定） |

---

## 附录：从零部署（新服务器）
如果这是**另一台全新服务器**（没有 Docker / cloudflared），按 open-connector 教程的通用章节先补齐基建：

1. **装 Docker**：
   ```bash
   curl -fsSL https://get.docker.com | sudo sh -s -- --mirror Aliyun
   sudo usermod -aG docker $USER && exit   # 重登生效
   docker --version && docker compose version
   ```

2. **Cloudflare 接管域名 DNS**：已为 `renzhengfeng.top` 做过，无需重复（同域名共用）。

3. **装 cloudflared + 建隧道 + systemd**：见 open-connector 教程第 4 节。建好隧道后，`config.yml` 直接用本教程第 4 步的完整版（含三条 hostname），再 `route dns` 加 `seacher.renzhengfeng.top`。

4. 其余步骤与本教程第 1~5 步一致。
