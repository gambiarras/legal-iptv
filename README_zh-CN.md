# Legal IPTV

<!-- hy-mt2-i18n:start -->
[English](./README.md) | **中文** | [日本語](./README_ja.md) | [Español](./README_es.md)
<!-- hy-mt2-i18n:end -->


将各类公开的 IPTV 及直播资源整合为静态的 M3U 播放列表。

该项目汇集了来自多个来源的频道，例如：

- IPTV-ORG  
- 来自 `extra_channels.json` 的人工精选频道  
- 由 `live-stream-catalog` 生成的频道

生成的输出是一个静态的 `playlist.m3u` 文件，无需付费托管即可通过 GitHub 发布。

---

## ⚠️ 免责声明 / 法律通告

该仓库**不存储、传输、重播或分发任何音视频内容**。

它仅会：

- 从公开可获取的来源收集元数据及流媒体地址  
- 将这些来源整合为单个播放列表  
- 通过机器可读的M3U格式输出，简化对这些公开流媒体地址的访问

所有内容：

- 由原始平台、提供商、广播机构或 CDN 直接提供
- 仍由相应的内容所有者、广播机构和平台负责
- 可能会因可用性变化、地域限制、许可约束、平台政策而随时被移除

本项目：

- 不会绕过付费墙、身份验证系统、数字版权管理措施或访问控制机制  
- 不会对媒体内容进行修改、重新传输、镜像、代理或重新托管  
- 不保证所列任何流媒体的合法性、授权情况、可用性、正常运行时间或长期有效性

生成的播放列表仅用于提供信息及便利之用。

用户有责任自行确保遵守以下各项规定：

- 当地法律法规  
- 版权及邻接权规定  
- 平台服务条款  
- 其所在司法管辖区适用的任何合同或许可限制

如果发现有任何频道、流或源未被列出，应采取的适当措施是从源配置或上游目录中将其移除。

# 严格约束
1. **结构锁定**：绝对保持原有的 Markdown 数据结构、缩进、标题层级、表格、链接、URL、徽章、代码块和行内代码完全不变。
2. **选择性翻译**：仅翻译面向用户展示的可见自然语言内容。
3. **禁止修改**：**严禁**翻译或更改代码标签、键名、变量占位符（如 {{var}}、${var}、%s、%d 等）、命令示例、文件路径、项目名、API 名、包名、模型名、标识符和代码符号；除非背景信息中已经给出对应译名。
4. 术语、风格、专有名词的译法要与所给背景信息保持一致。

---

## 目标

- 在无需付费托管的情况下生成公开的 `playlist.m3u` 文件
- 汇聚合法且可公开访问的流媒体源
- 安全地使用 `live-stream-catalog` 的输出内容
- 保持项目的可维护性与可扩展性
- 为未来整合更多源地址做好代码库准备

## 工作原理

## 工作原理

该项目会从不同的来源获取频道信息并合并它们：

### 1. IPTV-ORG
从IPTV-ORG的公共数据集中加载频道元数据、流地址以及标识图标。

### 2. 其他频道
从 `extra_channels.json` 中加载人工筛选后的频道。

### 3. live-stream-catalog
从 `live-stream-catalog` 仓库中加载已动态解析出的频道。

该数据源可能包含诸如以下的元数据：

- `stream_url`：流媒体地址  
- `status`：状态  
- `resolved_at`：解析时间  
- `expires_at`：过期时间  
- `ttl_seconds`：有效期（秒）

聚合流程会筛选并挑选频道，随后生成最终的 M3U 播放列表。

### 1. IPTV-ORG
从 IPTV-ORG 的公共数据集中加载频道元数据、流地址以及标识图。

## 本地使用方式

在同一台机器上运行这两个仓库时，请使用本地的 `live-stream-catalog` 输出结果：

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --output playlist.m3u \
  --meta-output playlist.meta.json
```

如果指定了`--live-catalog-file`参数，该文件必须存在。这样就能避免在本地运行时悄悄回退到远程目录。

`live-stream-catalog` 中的频道比手动筛选的额外频道以及 IPTV-ORG 频道具有更高的选择优先级。如果有多个候选频道指向相同的 URL 且名称相同或极为相似，只会保留最优的那个。若它们的 URL 不同，则会同时保留，并对重复的频道 ID 进行去重处理。

可选地在生成播放列表之前验证流地址的合法性：

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --validate-streams \
  --validation-max-workers 32 \
  --validation-timeout 6
```

默认情况下会禁用验证功能，因为它需要对每个唯一的流地址进行网络检测。启用该功能后，它会将每个地址的最新状态写入 `stream-status.json` 文件中。

在计划部署的环境中，可在该仓库外部定期执行验证，例如每4小时一次。常规的播放列表生成过程会读取 `stream-status.json`，并跳过那些最近被标记为离线的URL。

```bash
python3.11 -m legal_iptv \
  --live-catalog-file../live-stream-catalog/channels.json \
  --stream-status-file stream-status.json \
  --stream-status-max-age 14400
```

只有更新时间早于 `--stream-status-max-age` 参数所指定值的离线状态才会被采用，因此过时的故障不会永久阻断频道播放。

## 开发
运行单元测试：

```bash
python3.11 -m unittest discover -s tests
```

## 开发

运行单元测试：

```bash
python3.11 -m unittest discover -s tests
```

## 项目结构

```text
legal_iptv/
  models/       # 领域模型
  io/           # 文件持久化辅助工具
  clients/      # HTTP 客户端抽象层
  sources/      # 各来源的频道获取模块
  services/     # 聚合、筛选及元数据处理逻辑
  exporters/    # 播放列表生成模块
  resources/    # 额外频道等静态资源
```

## 项目结构

legal_iptv/
  models/       # 领域模型
  io/           # 文件持久化辅助工具
  clients/      # HTTP 客户端抽象层
  sources/      # 各数据源的频道获取模块
  services/     # 数据聚合、筛选及元数据处理逻辑
  exporters/    # 播放列表生成模块
  resources/    # 额外频道等静态资源
