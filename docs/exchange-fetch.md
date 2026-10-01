# 兑换所官方静态目录

```sh
python3 exchange_fetch.py --solis-dir /path/to/SolisClient --app-version CURRENT_VERSION --account cache/collector/account.json
```

只复用既有专用 collector，无创建账号选项。默认使用同版本缓存认证，`--allow-auth-refresh` 显式允许认证/刷新。`--initialize-day` 仅在服务端 1003 时允许一次日初始化并重试，可能领取专用账号登录奖励；1002 最多更新 Master 一次，401/UNAUTHENTICATED 仅在允许认证时恢复一次。共享 Shop 的受控恢复实现。

Exchange 业务调用仅 `List`，不调用 Execute/TryOn。默认输出 `cache/exchange/current.json`，可指定 `--output`。API 的所有 booths 和 exchanges 都被遍历。白名单包括名称、介绍、稳定 ID、顺序、素材、兑换资源 ID 和所需数量；不保存余额、持有数量、剩余库存、购买/解锁状态、动态重置时间或 CommonResponse。

稳定身份为 booth.id + exchange.id + name/description 字段。重复商品相同字段内容一致允许；冲突记录字段名并标 complete=false。请求失败输出白名单安全错误码且退出 1，不写空目录成功。成功请求但数据冲突/无效 ID 保留诊断快照并退出 1。

完整性仅指当前账号 API 返回目录，不能声称所有历史活动或所有账号均可见。奖励可由 rewardId 对照 Master Reward/Item 已审基础译文；不得据奖励猜测礼包标题。
