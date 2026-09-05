# 全网资源聚合搜索（seacher_resource）

一次搜索，并发查询**书籍 / 视频 / 音频 / 网盘分享 / 磁力链接**等多类资源；
每个结果标注**是否可用**（链接有效、资源存在）与**是否付费**（免费 / VIP / 单次付费）。

> ⚠️ **免责声明**：本项目仅供个人学习研究。不存储、不缓存任何资源文件本体，仅实时检索
> 公开分享链接与元数据。网盘/磁力源结果的版权归原上传者与权利人。请勿用于盈利或商业用途。

## 快速开始

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. （可选，启用网盘/磁力搜索）部署 PanSou sidecar —— 见下文「部署 PanSou」

# 3. 启动
uvicorn app.main:app --port 8000

# 4. 打开浏览器
# http://127.0.0.1:8000
```

无 `config.yaml` 时使用内置默认配置（中文源开启，国际源关闭）。复制 `config.example.yaml`
为 `config.yaml` 可自定义源开关、代理、限流参数——**改配置无需重启**（每次搜索热加载）。

### 访问密码（可选）

复制 `.env.example` 为 `.env`，设置 `ACCESS_PASSWORD=你的密码` 后，访问站点会先进入一个
只有密码框的登录页，输入正确密码后种 cookie 进入。留空则无密码（本地开发默认）。

## 数据源

| 源 | 区域 | 类型 | 接入方式 | 付费判定 | 默认 |
|---|---|---|---|---|---|
| Bilibili | 国内 | 视频 | 游客态 wbi 签名 API | pay badge + view 详情（充电专属/付费） | 开 |
| 网易云音乐 | 国内 | 音频 | web 搜索接口（免登录） | `fee` 字段（0/8=免费, 1=VIP, 4=购买） | 开 |
| 豆瓣 | 国内 | 影视/书 | suggest 接口（限流敏感，≥2s 间隔） | 目录站，恒 unknown | 开 |
| PanSou | 国内（自托管） | 网盘/磁力 | 自托管容器 API（需部署） | 分享链接，恒 free | 开* |
| Internet Archive | 国外 | 视频/音频/书 | 官方 API | 恒 free | 关† |
| Open Library | 国外 | 书 | 官方 API | public=免费, borrowable=可借阅 | 关† |
| Gutenberg | 国外 | 书 | gutenberg.org 目录搜索 | 恒 free | 关† |
| LibriVox | 国外 | 有声书 | archive.org 检索（librivoxaudio） | 恒 free | 关† |

\* PanSou 未部署时自动降级（该源报错，不影响其他源）。
† 国际源为 on-demand：在 `config.yaml` 配 `proxy:` 后，前端勾选「国际源」才按需查询（默认不查）。

## 部署 PanSou（网盘/磁力搜索）

[PanSou](https://github.com/fish2018/pansou)（MIT 开源）是网盘搜索聚合服务，自托管部署。
**实测可用的 PowerShell 命令**（2026-09，podman 5.6）：

```powershell
podman run -d --name pansou -p 8888:8888 -e "CHANNELS=tgsearchers6" -e "ENABLED_PLUGINS=dyyjpro,duoduo,djgou,feikuai,gaoqing888,gying,hdmoli,haitunsou,hunhepan,ikantv,jutoushe,kkv,dy4k,libvio,lingjisp,lou1,melost,meitizy,miosou,nyaa,ouge,panlian,pansearch,qqpd,quark4k,quarksoo,quarktv,qupanshe,sousou,thepiratebay,ting77,wanou,weibo,xb6v,xiaokupan,xiaozhang,xiaoyu,yingso,yulinshufa,yunso,yunsou,zlxapp,zxzj,rrbt,quarkres,diduan,erxiao,huban,labi,muou,shandian,zhizhen" -e "AUTH_ENABLED=false" --restart unless-stopped ghcr.io/fish2018/pansou:latest
```

实测要点（踩过的坑）：
- **`AUTH_ENABLED=false` 必须显式传**：新版镜像默认开启认证，否则所有请求返回 `AUTH_TOKEN_MISSING`
- **`CHANNELS` 用逗号分隔**（不是 `|`）；TG 频道源需代理才能连通（国内直连被墙），无代理时网盘结果主要来自**插件源**，所以 `ENABLED_PLUGINS` 要启用足够多的插件（官方全量 60+）
- **首次搜索可能返回 0 条**：PanSou 异步模式（4s 快速响应 + 后台补全缓存），同一关键词隔几秒搜第二次即有结果
- 有 socks5 代理时可加 `-e "PROXY=socks5://host.containers.internal:7890"` 并换用官方完整 TG 频道清单（见 pansou 仓库 docker-compose.yml）
- 部署后验证：`curl.exe -X POST http://127.0.0.1:8888/api/search -H "Content-Type: application/json" -d '{\"kw\": \"三体\"}'`
- 部分插件（如 melost）返回 GBK mojibake 坏数据，`pansou.py` 中的 `_fix_mojibake` 会尝试逆转修复

## API

| 端点 | 说明 |
|---|---|
| `GET /api/search?q=三体&types=video,audio,book,netdisk_share,magnet` | 聚合搜索，返回分组结果 + 失败源列表 |
| `POST /api/probe` `{resource_id, url, source, pan_type, type}` | 单条实时可用性探测 |
| `GET /api/providers` | 各源启用状态 |

交互式文档：`http://127.0.0.1:8000/docs`

## 可用性 / 付费判定

- **付费**：搜索结果元数据（网易云 fee、B 站 pay badge）→ top N 详情 enrich（B 站 view 接口
  判充电专属）→ 公版源恒免费。不解析付费墙页面。
- **可用性**：百度网盘按页面特征词判定（「啊哦你来晚了」=失效，`Accept-Encoding: identity`
  绕过假 gzip 头）；夸克走官方 token/detail 接口；其他链接 HEAD→GET(Range) 探测；
  403/412/429 判 `unverified`（反爬拦截 ≠ 资源失效）；磁力不探测。
- **限流**：每域名并发 2 / 全局 10，百度 share 页 ≥2s 间隔，探测结果 TTL 1h 缓存。

## 开发

```bash
python -m pytest tests/ -q          # 单测（31 个，respx mock 不依赖真网）
python scripts/probe_sources.py     # Phase 0 端点实测（决定源可用性）
python scripts/smoke.py             # 端到端冒烟
```

目录结构与设计详见 [PLAN.md](PLAN.md)。

## 合规边界

- 不存储/缓存资源本体，仅索引公开链接
- 真实 UA + 低频限流（每源 ≤1 QPS、百度 ≥2s 间隔）+ TTL 缓存
- 不破解验证码/字体加密/登录墙；被 403/412 拦截时标记 `unverified` 并退避
- 豆瓣 subject_search 解密、腾讯/爱奇艺 VIP 解析接口等明确排除
