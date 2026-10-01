# 清单、资源与剧情转换

从工具根目录执行命令。安装方式见 [README](../README.md)。

## 获取 Octo 清单

从自己的游戏设备复制 `octocacheevai` 到 `cache/octocacheevai` 后，可只解密清单：

```sh
python3 main.py --source cache --manifest-only
```

API 模式需要当前游戏运行时的 `Url`、`AppId`、`Version`、`ClientSecretKey` 和 `A`（AES passphrase）。可将捕获值保存在 `cache/octo-settings.json`，或设置 `IDOLY_OCTO_URL`、`IDOLY_OCTO_APP_ID`、`IDOLY_OCTO_VERSION`、`IDOLY_OCTO_CLIENT_SECRET_KEY`、`IDOLY_OCTO_AES_PASSPHRASE`。已有原始 AES 密钥时可改用 `IDOLY_OCTO_AES_KEY_HEX`。`IDOLY_OCTO_SETTINGS_FILE` 可指定其他本地 JSON 文件；环境变量优先于捕获文件，捕获文件优先于 `config.ini`。

```sh
python3 main.py --source api --reset --manifest-only
```

首次使用 `--reset` 建立清单；后续请求按 revision 检查更新。`cache/OctoManifest.json` 是资源索引，`cache/OctoPending.json` 保存待处理变更。首次建清单时待处理列表包含全部资源，下载前建议用名称或前缀限定范围。

## 按需下载和解包

```sh
python3 main.py --source api --name adv_example.txt --download-type resource --no-images
python3 main.py --source api --prefix adv_main_ --download-type resource --no-images
python3 main.py --source api --download-changed --prefix adv_ --download-type resource --no-images
python3 main.py --source api --name RESOURCE_NAME --download-type ab
```

`--download-type` 可取 `resource`、`ab` 或 `ALL`；只有明确需要全部资源时才使用 `--download-all`。下载内容按清单校验大小与 MD5，合格缓存可复用。图片提取到 `cache/image/Texture2D/`。

## 获取 MasterDB

`master_fetch.py` 使用 [SolisClient](https://github.com/vilebbit/SolisClient) 的客户端流程取得 MasterTag，按表下载并解密为 JSON。需要 SolisClient 的 protobuf 模块及 SQLCipher Python 驱动。

使用认证 XML 自动刷新过期 Firebase token 时，将游戏客户端的 API key 保存在 `cache/firebase-settings.json`：

```json
{"api_key": "<游戏客户端的 Firebase API key>"}
```

也可设置 `IDOLY_FIREBASE_API_KEY`，或用 `IDOLY_FIREBASE_SETTINGS_FILE` 指定其他 JSON 文件。读取顺序与 Octo 配置一致：环境变量、缓存 JSON、`config.ini` 的 `[Firebase settings] API_KEY`。未过期的 token 和本地 `--master-tag` 模式不需要该 key。

```sh
python3 master_fetch.py --solis-dir /path/to/SolisClient --master-tag /path/to/master-tag.pb
# 或使用本机临时认证文件取得当前 MasterTag：
python3 master_fetch.py --solis-dir /path/to/SolisClient \
  --auth-xml /path/to/temporary-firebase-auth.xml --app-version <当前游戏版本>
```

默认输出到 `cache/masterdata/`。`--table` 可限定单表；重复获取会校验版本和缓存，失败后重新执行可续跑。

## 剧情 TXT ↔ CSV

下载 `adv_*.txt` 后，导出 CSV 并填写 `trans` 列，再合并成翻译后的 TXT：

```sh
python3 adv_csv.py export --file adv_example.txt
# 编辑 cache/csv/ 中对应 CSV 的 trans 列
python3 adv_csv.py merge --file adv_example.txt \
  --name-glossary /path/to/name-glossary.json
```

`--name-glossary` 指向人名 JSON 字典，例如 Hoshimi_Teleprompter 提供的词典。CSV 的 `name` 列仅作上下文；人名在合并时统一替换。源 TXT、CSV 和输出目录可用 `--source-dir`、`--csv-dir`、`--output-dir` 指定；默认输出到 `cache/local-files/resource/`。

可选的 `patch` 命令把已填写的正文和标题译文保存为精简 JSON，便于版本管理和以后重新导出 CSV：

```sh
python3 adv_csv.py patch --file adv_example.txt
python3 adv_csv.py export --file adv_example.txt --patch-dir cache/patches
python3 adv_csv.py status --name-glossary /path/to/name-glossary.json
```

补丁默认写入 `cache/patches/`，含源文件哈希与发生变化的译文；它不生成 TXT。`status` 统计补丁和人名表的覆盖率。`--file` 可重复指定，`--prefix` 可按前缀选择；两者省略时处理全部已下载剧情。

合并前会核对源文件哈希、字段 ID、原文、占位符与字面 `\n` 数量。空译文保留原文，非文本指令保持不变。`export` 会写入目标 CSV，已有校对内容时应先保存补丁或备份。

## 图片文字回填

如需翻译图片中烘焙的文字，可将修好的同尺寸 PNG 写回目标 AssetBundle：

```sh
python3 main.py --source api --name IMAGE_RESOURCE_NAME --download-type ab
python3 asset_patch.py --bundle cache/asset/image/IMAGE_RESOURCE_NAME \
  --image /path/to/translated/IMAGE_RESOURCE_NAME.png
```

默认输出到 `cache/local-files/asset/`。

