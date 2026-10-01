# 商店官方文本采集

使用已有专用 collector 账号；没有创建账号选项。默认仅复用当前版本缓存认证，不自动登录、刷新或日常初始化。

```sh
python3 shop_fetch.py --solis-dir /path/to/SolisClient --app-version CURRENT_VERSION --account cache/collector/account.json
```

认证失效可显式添加 `--allow-auth-refresh`，允许已有账号认证和刷新。`--initialize-day` 仅允许收到服务端 1003 时对专用 collector 日常初始化一次并重试（可能领取登录奖励）；不会在每次采集前初始化。1002 最多更新 Master 一次，401/UNAUTHENTICATED 仅在允许认证时恢复一次。没有有效缓存认证时需要同时允许认证。

默认输出 `cache/shop/current.json`，可用 `--output` 指定。Shop 业务调用仅 List、ListItem、GetLoginBonusPackageItem（type 3）、GetConditionRewardPackageItem（type 4）。遍历 API 返回的全部商店，不依赖横幅逐页点击。

输出白名单官方名称/介绍、静态 ID、顺序及素材引用；不保存 CommonResponse、库存、购买状态或认证头。每个商店记录方法成功/失败、返回数量和唯一商品数。失败不会当作空列表成功，部分结果退出码为 1。范围仅当前账号 API 返回目录，不代表全部历史活动或所有账号可见商品。图片横幅本身没有文字字段，需另行核验图片中的文字。
