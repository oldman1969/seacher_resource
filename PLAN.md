# 全网资源聚合搜索（seacher_resource）实现计划

> 定位：个人学习用途的全网资源聚合搜索 Web 应用。一次搜索，并发查询书籍、视频、音频、网盘分享、磁力链接等多类资源；每个结果标注**是否可用**与**是否付费**（免费/付费/VIP）。
>
> 本文档为项目需求与设计文档，开发按本文档执行。

---

## 1. 总体架构

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│  浏览器前端   │ ──▶ │  FastAPI 应用                             │
│ (静态HTML+JS)│ ◀── │  ├─ /api/search    聚合搜索               │
└─────────────┘     │  ├─ /api/probe     单条可用性探测           │
                    │  ├─ /api/providers 数据源状态               │
                    │  ├─ aggregator     并发调度/超时/错误隔离    │
                    │  ├─ probe          网盘/HTTP 可用性探测      │
                    │  └─ providers/*    插件式数据源适配器        │
                    └───────┬──────────────────────┬───────────┘
                            │                      │
              官方/非官方 API 直连          PanSou 容器（sidecar, :8888）
              (B站/网易云/IA/OL/…)         网盘+磁力聚合(100+插件)
```

**技术选型**

- Python ≥ 3.12 + FastAPI + uvicorn + httpx（AsyncClient，连接池 / per-request timeout）
- Pydantic v2 + pydantic-settings（统一模型 + YAML 配置 + .env 敏感项）
- 前端：无构建步骤的静态 HTML + 原生 JS（搜索页 + 按类型分组 + 标签高亮）
- 测试：pytest + pytest-asyncio + respx（mock httpx，单测不依赖真网）
- 网盘/磁力通道：自托管 [PanSou](https://github.com/fish2018/pansou)（Go, 14k+ stars, 活跃维护）作为 sidecar 服务，docker-compose 内网调用
- 明确不引入：Playwright 无头浏览器、Celery/Redis、数据库（Phase 3 才考虑 SQLite 缓存）

## 2. 统一数据模型（app/models.py）

```python
class ResourceType(str, Enum):
    book = "book"             # 书籍/文本
    video = "video"           # 视频
    audio = "audio"           # 音乐/播客/有声书
    netdisk_share = "netdisk_share"  # 网盘分享链接
    magnet = "magnet"         # 磁力/ed2k 链接

class PaymentStatus(str, Enum):
    free / paid / vip / unknown

class AvailabilityStatus(str, Enum):
    available / unverified / unavailable

class Resource(BaseModel):
    resource_id: str            # "<source>:<external_id>"
    title: str
    type: ResourceType
    source: str                 # provider 名
    url: str                    # 落地页或 magnet: 链接
    payment: PaymentStatus = unknown
    payment_note: str | None    # "大会员专属" / "¥12.99" 等
    availability: AvailabilityStatus = unverified
    pan_type: str | None        # 网盘类型: baidu/quark/aliyun/xunlei/115/uc/tianyi/123/…
    password: str | None        # 网盘提取码
    cover / author / publish_date
    extra: dict                 # rating、播放量、duration、文件大小等
```

## 3. Provider 插件体系（app/providers/base.py）

```python
class BaseProvider(ABC):
    name: str
    supported_types: tuple[ResourceType, ...]
    enabled_by_default: bool

    async def search(self, keyword, limit=10) -> list[Resource]     # 抽象方法
    async def enrich(self, r: Resource) -> Resource                  # 可选：补付费/可用性元数据
    async def health_check(self) -> bool
```

- 注册表 `registry.py` 按 config 的 `enabled` 列表实例化
- 所有 search 内部异常转为 `ProviderError(source, reason)` —— **单源失败绝不影响其他源**，失败源记入响应 `errors` 并在 `/api/providers` 标记 degraded
- 聚合器 `aggregator.py`：`asyncio.TaskGroup` 并发查所有源，每源整体超时 8s；对每类型 top N 调 `enrich()`（单条 3s 超时）
- 排序 `ranking.py`：`score = 标题相关度 + log(热度) + bonus(free=2, unknown=0.5, paid/vip=0)`

## 4. 数据源清单与优先级（2026-09 调研结论）

### MVP（第一期）

| 源 | 类型 | 接入方式 | 付费判定 | 稳定性 |
|---|---|---|---|---|
| Internet Archive | video/audio/book | 官方 `advancedsearch.php` API，无鉴权 | 恒 free | 高（官方） |
| Open Library | book | 官方 `search.json` + availability API | free / 可借阅 | 高（官方） |
| Project Gutenberg | book | gutendex.com API | 恒 free | 中 |
| LibriVox | audio | 官方 `api/feed/audiobooks` | 恒 free | 高（官方） |
| Bilibili | video | 非官方 wbi 签名 API（社区文档持续维护），需 buvid cookie | 搜索结果 pay badge + `view` 详情 `rights.pay`/`is_upower_exclusive` | 中高 |
| 网易云音乐 | audio | 直连 `music.163.com/api/search/get`（实测可用，无需登录） | `fee` 字段：0/8=free, 1=vip, 4=paid —— **判定能力极佳** | 中 |
| **PanSou 自托管** | **netdisk/magnet** | 自部署容器，`POST /api/search {"kw": …}`，结果自带 pan_type 分类（baidu/quark/aliyun/xunlei/115/uc/magnet/ed2k…） | 网盘资源默认 free（分享），磁力默认 free | 中（自托管 + 上游社区维护 100+ 站点插件） |
| 网盘链接探测模块 | netdisk | 见 §5.2 | — | — |

### Phase 2

- **YouTube Data API v3**（官方，需 API key；配额极紧：10000 units/天，search 一次 100 units ≈ 100 次搜索/天，**必须加搜索结果缓存 + 配额计数降级**；搜索结果无付费标志，付费判定弱）
- **Spotify**（Client Credentials OAuth → `/v1/search`；订阅制全库，标记 vip）
- **Podcast Index**（官方免费 API key，播客目录，全 free）
- **TED**（graphql.ted.com 官网自用 GraphQL，全 free）
- **网易云音乐 API enhanced 自托管**（仅当直连被风控时启用）
- **豆瓣 suggest**（元数据增强：给 book 结果补评分/封面，标 unverified；subject_search 已加密，**明确不解密**）
- **喜马拉雅**（非官方接口，失败自动禁用）

### Phase 3（可选）

- 直连聚合站备份 Provider（移植 pansou 的 Go 插件逻辑为 httpx + 正则，1–2 个，作 PanSou 宕机兜底）
- 腾讯视频/爱奇艺 HTML 解析（best-effort，无官方 API，选择器随时失效，feature flag 控制）
- Openverse（CC 授权图片/音频，官方免费 API）
- 微信读书（需登录 cookie + 瑞数反爬，默认禁用）
- SQLite 缓存、Dockerfile、前端「仅看免费」过滤

### 明确不做

- 豆瓣 subject_search 解密（字体加密 + `window.__DATA__`，加密方案轮换）
- 腾讯/爱奇艺 VIP 解析类灰色接口
- 自建网盘索引库（存储索引的法律暴露面远大于实时检索公开结果）
- 已消失的聚合站（易搜、学霸盘等）与已退役 API（Coursera catalog）

## 5. 关键模块设计

### 5.1 Bilibili wbi 签名（app/utils/wbi.py）

按社区文档实现：GET `bilibili.com` 拿 buvid cookie → `x/web-interface/nav` 拿 img_key/sub_key → mixinKeyEncTab 混淆 → MD5 生成 `w_rid/wts` 签名 → 调 `x/web-interface/wbi/search/type`。搜索 `search_type=video` + `media_bangumi/media_ft`（番剧/影视，结果含 pay badge：会员→vip，付费→paid）。密钥缓存复用。

### 5.2 网盘分享链接可用性探测（app/probe.py 的 netdisk 部分）

移植自开源项目 [NetDiskLinkValidator](https://github.com/fishforks/NetDiskLinkValidator) 的已验证判定逻辑（httpx 异步化）：

**百度网盘**（GET `pan.baidu.com/s/{surl}`，浏览器 UA，follow_redirects，保留 `?pwd=` 参数）：
- 页面含 `分享的文件已经被取消` / `分享已过期` / `你访问的页面不存在` → unavailable
- 含 `请输入提取码` / `提取文件` → available（带提取码）
- 含 `过期时间` / `文件列表` → available

**夸克**（走官方接口）：
1. `POST drive-h.quark.cn/1/clouddrive/share/sharepage/token` `{pwd_id, passcode:""}` 拿 stoken
2. 查 `sharepage/detail`；错误码 `NOT_FOUND/SENSITIVE_RESOURCE/EXPIRED` → unavailable，`PASS_CODE_EMPTY` → available 带码

其他网盘（uc/aliyun/115/123/天翼/迅雷）的域名与分享 ID 正则清单直接复用 NetDiskLinkValidator 第 230–260 行。

**限速**：百度 share 页 ≥2s 间隔 / 全局并发 ≤2，防风控连坐。

**磁力链接**：不做 DHT 探测（成本过高），默认 `unverified`。

### 5.3 通用 HTTP 可用性探测（app/probe.py）

- HEAD 优先（connect 3s / read 5s），403/405 退化 GET + `Range: bytes=0-0`
- 状态映射：2xx/3xx → available；404/410 → unavailable；**403/412/429 → unverified（反爬拦截 ≠ 资源失效）**；超时/DNS 失败 → unavailable
- per-domain 信号量（每域名并发 2、全局 10）+ TTL 1h 内存缓存
- **API 探测优先于 HTTP 探测**（如 B 站用 `view` 接口 `code==0` 判活，避免 GET 页面被风控产生假阴性；OpenLibrary 用 availability API）
- MVP 每类型同步探测 top 3，其余 unverified；前端卡片「验证」按钮调 `POST /api/probe` 实时探测

### 5.4 付费判定（三层，按成本递增）

1. **搜索结果自带元数据**（零成本）：网易云 `fee`、B 站 pay badge、公版源恒 free
2. **enrich 仅对 top N 查详情**：B 站 `view` 的 `rights.pay` / `is_upower_exclusive`
3. **不做**：解析付费墙 HTML 页面

### 5.5 API 设计

- `GET /api/search?q=&types=video,book,audio,netdisk_share,magnet` → `{results: {type: [...]}, errors: [...]}`
- `POST /api/probe` body `{resource_id, url, source, pan_type?}` → `{availability, checked_at}`
- `GET /api/providers` → 各源启用状态与健康度

### 5.6 前端（static/）

搜索框 + 五个类型 Tab（视频/书籍/音频/网盘/磁力）；卡片网格：封面、标题、作者/UP主、来源、时间、文件大小（网盘/磁力）、提取码（一键复制）；标签：绿「免费」、橙「VIP」、红「付费」、灰「未知」；可用性角标 + 未验证项点击验证。约 300–400 行原生 JS，无构建步骤。

### 5.7 配置（config.yaml + .env）

```yaml
providers:
  bilibili: {enabled: true}
  internet_archive: {enabled: true}
  openlibrary: {enabled: true}
  netease: {enabled: true}
  gutenberg: {enabled: true}
  librivox: {enabled: true}
  pansou: {enabled: true, base_url: "http://pansou:8888"}   # 网盘+磁力聚合
  youtube: {enabled: false}    # Phase 2，需配额管理
  douban: {enabled: false}
  ximalaya: {enabled: false}
probe: {per_domain_concurrency: 2, baidu_min_interval: 2.0, timeout_connect: 3, timeout_read: 5, cache_ttl: 3600, auto_probe_top: 3}
search: {deadline: 8, per_source_limit: 10}
```

`.env`：`BILI_SESSDATA`（B 站被风控时启用）、`YOUTUBE_API_KEY`、`SPOTIFY_*`、`PODCASTINDEX_*` 等。

## 6. 项目结构

```
seacher_resource/
├── PLAN.md                   # 本文档
├── docker-compose.yml        # app + pansou sidecar
├── pyproject.toml
├── config.example.yaml / .env.example
├── app/
│   ├── main.py               # FastAPI 工厂、lifespan（共享 AsyncClient）、静态文件
│   ├── config.py / models.py / aggregator.py / probe.py / ranking.py
│   ├── api/routes.py
│   ├── utils/http.py         # AsyncClient 工厂
│   ├── utils/wbi.py          # B 站 wbi 签名
│   ├── netdisk/validators.py # 网盘分享链接判定（百度/夸克/…）
│   └── providers/
│       ├── base.py / registry.py
│       ├── bilibili.py / internet_archive.py / openlibrary.py
│       ├── gutenberg.py / librivox.py / netease.py
│       ├── pansou.py         # 网盘+磁力聚合（netdisk_share + magnet）
│       └── …(phase2: youtube.py / spotify.py / douban.py / ximalaya.py …)
├── static/                   # index.html / app.js / style.css
├── scripts/
│   ├── probe_sources.py      # Phase 0：逐端点实测报告
│   └── smoke.py              # 端到端冒烟
└── tests/                    # conftest + test_wbi / aggregator / probe / netdisk + providers/fixtures
```

## 7. 实施步骤

### Phase 0 —— 端点实测（半天，先于编码）

> **✅ 已完成（2026-09-04 实测结论，实际结果与原计划差异较大）：**
>
> | 源 | 实测 | 决定 |
> |---|---|---|
> | Bilibili wbi 游客态 | ✅ 加 Referer 后每词 20 条 | 进 MVP |
> | 网易云音乐 | ✅ `fee` 字段验证成功 | 进 MVP |
> | 豆瓣 movie suggest | ✅（限流敏感） | 进 MVP |
> | 百度网盘分享探测 | ✅ 需 `Accept-Encoding: identity` 绕假 gzip 头；404+「啊哦」=失效 | 进 MVP |
> | IA / OpenLibrary / LibriVox / YouTube / Spotify | ❌ 直连超时且本机无代理 | Provider 就绪但默认禁用，配 `proxy:` 启用 |
> | 喜马拉雅 | ⚠️ 200 但 0 条（软风控） | Phase 2 |
> | gutendex | ⚠️ 不稳定（时通时断） | Phase 2 |
>
> **环境关键结论**：当前网络无代理时国际源全不可达 → MVP 转为中文源为主。
> 另：B 站标题含未配对 surrogate，需在模型层清洗；docker 环境为 podman。

写 `scripts/probe_sources.py` 对每个候选端点发真实请求（正确 UA），输出可用性报告。**决策门**：
- 实测通过的进 MVP
- B 站若游客态被风控（-412/-403）→ 要求 `.env` 配 `BILI_SESSDATA`，否则挪 Phase 2
- 网易云直连 API 若失效 → 改用 enhanced 版自托管

### Phase 1 —— MVP（3–4 天）

1. 脚手架：pyproject、config、models、base、registry（接口先行）
2. `internet_archive.py` + `openlibrary.py`，跑通 aggregator → API → 前端整条链路
3. `utils/wbi.py` + `bilibili.py`；`netease.py`（fee 字段映射）
4. docker-compose 起 PanSou + `pansou.py` Provider（pan_type/password 映射）
5. `netdisk/validators.py`（百度/夸克探测）+ `probe.py` 通用探测 + 前端标签与验证按钮
6. 单测（respx + fixtures）+ smoke 脚本

### Phase 2 —— 全网扩展（3–4 天）

YouTube（配额管理 + 缓存）、Spotify、Podcast Index、TED、豆瓣 suggest、喜马拉雅（自动降级）、gutenberg/librivox 若 Phase 1 未完成则补齐。

### Phase 3 —— 加固（视需求）

直连聚合站兜底 Provider、SQLite 缓存、每源令牌桶、Dockerfile 优化、前端过滤/分页。

## 8. 合规边界（写入 README，应用内放免责声明页）

- 本项目仅供个人学习研究，不得商用或盈利
- **不存储、不缓存任何资源文件本体**，仅实时检索公开分享链接与元数据
- 所有请求带真实 UA + 低频限流（每源 ≤1 QPS、百度 share 页 ≥2s 间隔）+ TTL 缓存
- 不破解验证码/字体加密/登录墙；被 403/412 拦截时标记 `unverified` 并退避，不重试硬闯
- 网盘/磁力源的结果版权归原上传者与权利人，应用仅提供链接索引

## 9. 验证方式

1. **Phase 0 决策门**：`python scripts/probe_sources.py` 实测输出直接决定 MVP 源选择
2. **单元测试**：wbi 签名（固定 key 断言 w_rid）；网易云 fee→PaymentStatus 映射；百度/夸克分享页文本→AvailabilityStatus 判定（用真实失效/有效样本 fixture）；probe 状态码映射逐条断言；PanSou 响应→Resource 映射
3. **集成测试**：`ASGITransport` 全链路，mock 三源 → 断言分组结构、单源 500 时其余源结果完好
4. **冒烟**：`python scripts/smoke.py` 搜「三体 / 郭德纲 / 红楼梦 / 让子弹飞」，校验响应 schema 与至少三类资源返回
5. **手动清单**：浏览器验证标签正确性（黄金样本：网易云 VIP 歌曲→橙标、B 站大会员番剧→橙标、公版书→绿标、一个已取消分享的百度网盘链接→红「失效」）；点「验证」观察可用性变化；提取码一键复制

## 10. 参考实现（外部）

- [fish2018/pansou](https://github.com/fish2018/pansou) —— 网盘搜索 sidecar；`plugin/` 目录 100+ 站点插件是数据源接入的「代码即文档」
- [fishforks/NetDiskLinkValidator](https://github.com/fishforks/NetDiskLinkValidator) —— 网盘分享链接判定逻辑（L179–260 可直接移植）
- [SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) —— B 站 wbi 签名与接口文档
- [laoma2053/awesome-zhuiju-free](https://github.com/laoma2053/awesome-zhuiju-free) —— 每日 CI 探活的站点清单，可用于动态校准
- [NeteaseCloudMusicApiEnhanced/api-enhanced](https://github.com/NeteaseCloudMusicApiEnhanced/api-enhanced) —— 网易云 API 备选自托管方案
