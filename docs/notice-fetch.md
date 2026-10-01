# 公告采集

公告列表来自游戏的官方 Notice API；正文是 `stat.game-idolypride.jp` 上可匿名下载的静态网页，不属于 MasterDB。工具分别保存列表、入口标题和正文，避免把列表短标题误当成正文 H1。

## 官方索引

需要 SolisClient 的生成协议文件、证书与 `client_config.json`，以及 grpcio、protobuf、requests、pycryptodome。只导入协议与传输模块，不运行 SolisClient 的完整登录脚本。

```sh
python3 -m pip install -r requirements-notice.txt
```

首次建立专用采集游客账号：

```sh
python3 notice_fetch.py official-index \
  --solis-dir /path/to/SolisClient --app-version <当前游戏版本> \
  --create-account --initialize-day
```

后续复用同一个账号：

```sh
python3 notice_fetch.py official-index \
  --solis-dir /path/to/SolisClient --app-version <当前游戏版本> \
  --initialize-day
```

`--create-account` 只允许本地没有账户缓存时创建一个 Firebase 游客。默认账户文件为 `cache/notice/collector-account.json`，其中令牌仅当前用户可读（0600）；每次运行复用缓存，自动刷新到期 Firebase 凭据，游戏会话失效时最多重新认证一次。Firebase API key 从既有本地配置读取，不写进代码。不要把用户实际游戏账号凭据填入专用采集账户文件。

必要链为 Firebase 游客/续期 → QSeed → System.Check → Auth.Login →（服务器要求时刷新 Firebase 声明并重新登录一次）→ Master.Get 取得当前版本 → Notice.List/FetchList。初次游客和跨日请求可能返回 `DateChanged(1003)`；只有显式传入 `--initialize-day` 才调用一次专用采集账号的 Home.Login，这可能授予该账号自动登录奖励，并设置它的基础音量/画质选项。不使用该选项时明确停止，不执行初始化。不会调用手动领奖、购买、玩法或公告已读接口。

默认索引保存到 `cache/notice/official-index.json`，只保留 `Notice.List` 返回的三分类首批，标记 `current_window`，并在 `_pagination.first_page_has_next` 保存各分类是否还有后续分页；它不是“全部未过期公告”。显式添加 `--archive` 才继续调用 `FetchList`，默认另存 `cache/notice/archive-index.json`。历史模式每分类最多 1000 个后续分页；达到限制时报错，绝不把截断结果写成全量。两个模式均只保留静态字段，过滤账号响应和关联令牌。只读网络暂时故障最多重试三次；创建账号及初始化不盲目重试。首次创建的结果不明时保留标记并停止，避免下一次误建第二个账号。

协议没有结束时间或 expired 字段。`startTime` 是发布时间，`displayNotification` 等字段不能据此解释为公告是否过期。游戏“加载更多”仍能展示历史条目，不能按正文日期或进入首页窗口与否自动删除缓存。日常采集与部署默认采用当前窗口，历史归档单独保留并显式选择。

## 正文与独立入口标题

```sh
python3 notice_fetch.py pages --input cache/notice/official-index.json
python3 notice_fetch.py entry-titles --input cache/notice/official-index.json
```

`pages` 默认写 `cache/notice/source.json`，结构为 `pages → 路径 → title/headline/texts`。输入也支持已有 pages 源文件或 paths 列表。只请求官方 HTTPS 静态主机，不带 Cookie/账号令牌、不跟随重定向。官方地址的公开 `_v=YYYYMMDD-HHMMSS` 缓存版本参数会移除，其它查询参数拒绝。游戏内跳转条目跳过。任一页失败时，此简单模式保留旧输出。

`entry-titles` 默认写 `cache/notice/entry-titles.json`，按 ID/分类独立保留 `title`、`listTitle`、`linkDetail`、来源 SHA256 和各标题字段 SHA256。两种标题不同或列表标题为空时均原样保留，不拿正文 H1 补齐。译文应以稳定 ID/字段和原文摘要关联。

## 全历史正文与断点续抓

```sh
python3 notice_fetch.py official-index \
  --solis-dir /path/to/SolisClient --app-version <当前游戏版本> \
  --initialize-day --archive
python3 notice_fetch.py archive-pages \
  --input cache/notice/archive-index.json --workers 4
```

默认每页缓存到 `cache/notice/page-cache/`，正文汇总为 `cache/notice/archive-source.json`，报告为 `cache/notice/archive-report.json`。并发可设 1–8，默认 4。每页保存来源、采集时间及摘要；再次运行优先验证并复用缓存，损坏缓存重新获取。上游可能修改旧公告，定期使用 `--refresh` 重新抓取以检查更新。

单页失败不阻断其它页：429、5xx、连接/超时最多尝试三次，其余失败单列进报告。成功页会逐步落盘，程序最终若有失败返回状态码 2；报告 `complete` 明确表示是否所有目标页成功。失败报告只记页面路径、错误类型、HTTP 状态和尝试次数，不输出请求凭据。未出现在当前索引中的历史缓存不会删除；建议每轮独立指定 `--output` 和 `--report` 保存快照，翻译流程增量合并，不能因页面离开当前窗口就删除旧译文。

## 已有游戏会话

```sh
python3 notice_fetch.py index --solis-dir /path/to/SolisClient --session cache/notice/session.json
```

此模式默认只用现有请求 metadata 调用 Notice.List；只有添加 `--archive` 才继续 FetchList，不创建或续期账号。会话必须保存在忽略的 cache 内。接口需要当前 app、master 版本、游戏令牌和必要设备字段；过期或跨日失败时由调用者更新，工具不擅自登录用户账号。优先使用上面的独立采集账号模式。

## 可选第三方交叉核查

`community-index` 可以匿名读取 INFO PRIDE 的公开缓存索引，不作为官方采集主流程。服务可能延迟或仅保留一个窗口；曾实测请求每类 100 条但各类仅返回 20 条，远少于官方全历史。该快照不能用来删除历史页面，也不能用作全量覆盖率分母。

## 协议参考

- [IDOLY-Backend](https://github.com/vilebbit/IDOLY-Backend)：AGPL-3.0；其公告接口读取已写入的 KV，并未直接向官方获取。
- [SolisClient](https://github.com/vilebbit/SolisClient)：提供官方协议和传输模块。工具依赖其生成协议，不执行包含 Home/玩法操作的完整运行入口。
- [Firebase REST](https://firebase.google.com/docs/reference/rest/auth)：游客身份与令牌续期接口。
