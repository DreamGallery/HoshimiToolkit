# 游戏条款静态文本

`rule_fetch.py` 通过官方 `api.Master/Rule` 只读获取标题页条款正文。该接口返回纯文本；实测仅需当前 `x-app-version`，不需要 Firebase、游戏账号或登录初始化。正文不属于公告网页。

```sh
python rule_fetch.py --solis-dir /path/to/SolisClient --app-version CURRENT_VERSION \
  --output cache/legal/source.json
```

运行环境使用 `requirements-notice.txt` 中的 gRPC 依赖；`--solis-dir` 提供协议生成模块和证书，不运行 SolisClient 的登录程序。请求各有 30 秒超时，任意请求失败保留原输出。

| ruleType | 类别 |
| --- | --- |
| 1 | 利用条款 |
| 2 | 隐私政策 |
| 3 | 资金结算法相关说明 |
| 4 | 特定商业交易说明（接口可返回空文） |
| 5 | 服务指南 |
| 6 | 第三方许可证（英文原样保留） |
| 7 | 外部传输说明 |

schema v1 包含完整 `source`、UTF-8 `source_sha256` 和逐行 `segments`。每个片段单独记录原始 `line_ending`，拼接后必须逐字等于完整原文，包括空行、CRLF 和末尾换行。所有类别均保留，空文不是抓取失败。该文件可直接作为 Hoshimi_Teleprompter 的 `legal_translate.py` 输入。

本地化资源独立于通用 UI 字典，避免把条款中的短句传播到其他游戏界面。官方更新后 SHA 不一致时应显式审核迁移译文，不能沿用旧全文匹配。
