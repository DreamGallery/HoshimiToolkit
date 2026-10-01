# HoshimiToolkit

《偶像荣耀》资源与官方文本采集工具，支持 Octo 下载、AssetBundle 解包、MasterDB、剧情转换、公告和结构化帮助。

## 安装与配置

需要 Python 3.12+：

```sh
python3 -m pip install -r requirements.txt
cp config.example.ini config.ini
```

在本地 `config.ini` 填写客户端配置和资源解密参数；可用 `IDOLY_TOOLKIT_CONFIG` 指向其他配置文件，`IDOLY_FILE_KEY` / `IDOLY_FILE_IV` 提供解密参数。Octo 参数按环境变量、本地捕获 JSON、配置文件顺序读取。配置、认证文件与生成数据不提交。

官方接口采集另需 `requirements-notice.txt` 和独立的 SolisClient 协议目录，通过 `--solis-dir /path/to/SolisClient` 指定；MasterDB 解密另需 SQLCipher 驱动。各项目无需放在同一父目录。

## 使用

各入口支持 `--help`，默认输出到本工具 `cache/`；显式输入输出路径相对于调用目录。资源存储路径可用 `IDOLY_ASSET_PATH`、`IDOLY_RESOURCE_PATH`、`IDOLY_UPDATE_PATH` 覆盖。

| 入口 | 用途与说明 |
| --- | --- |
| `main.py`、`asset_patch.py` | [资源下载、解包与图片回填](docs/resources.md) |
| `master_fetch.py` | [MasterDB 下载与解密](docs/resources.md#获取-masterdb) |
| `adv_csv.py` | [剧情 TXT / CSV 转换与补丁](docs/resources.md#剧情-txt--csv) |
| `notice_fetch.py` | [公告索引、正文与历史归档](docs/notice-fetch.md) |
| `help_fetch.py` | [结构化帮助正文](docs/help-fetch.md) |
| `rule_fetch.py` | [法律条款](docs/rule-fetch.md) |
| `shop_fetch.py`、`exchange_fetch.py` | [商店](docs/shop-fetch.md) / [兑换所](docs/exchange-fetch.md) |

采集账号、认证恢复和日初始化行为见对应文档。剧情导入会核对源哈希、字段和占位符；重新导出前保存校对补丁。共享人名表通过 `--name-glossary` 显式指定。

## 致谢

基于 [Vibbit/HoshimiToolkit](https://github.com/vilebbit/HoshimiToolkit)。
