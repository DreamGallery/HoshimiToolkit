# 官方帮助正文

`help_fetch.py` 使用官方 `api.Master/GetHelpCategory` 读取 Help (1)、Faq (2)、Tips (3)。不请求账号找回类型 (4)。请求仅携带当前 `x-app-version`，不读取账号或执行登录。

```sh
python help_fetch.py --solis-dir /path/to/SolisClient --app-version CURRENT_VERSION
```

依赖与 `rule_fetch.py` 相同。使用 SolisClient 的 `papi_grpc` 传输包装及证书；不要替换成没有传输包装的 `papi_pb2_grpc`。

输出：

- `cache/help/source.json`：来源、抓取时间、类型计数、完整层级数据和正文摘要。
- `cache/help/HelpCategory.json`：与 MasterDB 同结构的分类数组；主项目导入前需要按稳定 ID 对齐现有数据顺序。

保留 `id/type/title/order/targetTypes/contents`，以及子条目的 `helpContentId/title/text/order/targetTypes/assetIds`。换行及正文不做清洗。完成全部请求与校验后才写入输出，网络错误保留已有文件。

“もし恋”的分类 ID 是 `help_content-help-love`，实际属于 Tips (3)；“遊び方”子条目为 `help_content-help-love-02`。这些内容来自结构化帮助数据，无需公告 HTML 抓取。主项目已有 HelpCategory 翻译时，应先按原文变化做差异核对，再仅翻译新增或变化的条目。

注意：API 和 MasterDB 的 `contents` 数组顺序可能不同。必须按分类 `id` 和子条目 `helpContentId` 比较文本，不可直接覆盖现有按 `contents[n]` 定位的翻译。活动入口也可能打开公告网页；应以实际入口的数据类型选择公告或结构化帮助采集。
