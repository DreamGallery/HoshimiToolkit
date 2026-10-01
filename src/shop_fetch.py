"""Read current collector-visible Shop directory; never create accounts."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from .notice_official import OfficialCollector, CollectorError, account_lock

SHOP_FIELDS = ('id', 'name', 'type', 'order', 'thumbnailAssetId', 'bannerAssetId')
ITEM_FIELDS = ('id', 'name', 'description', 'productId', 'consumptionId', 'rewardId', 'order', 'thumbnailAssetId')
METHODS = {'List', 'ListItem', 'GetLoginBonusPackageItem', 'GetConditionRewardPackageItem'}

class ShopReadError(CollectorError):
    def __init__(self, codes=()):
        self.safe_codes = sorted(set(codes) & {'1002', '1003', '401', 'UNAUTHENTICATED'})
        super().__init__('Shop read failed')

def recover_read(call, classify, *, refresh_auth, update_master, initialize_day, allow_auth=False, allow_day=False):
    recovered = set()
    def read(method, shop_id):
        while True:
            try:
                return call(method, shop_id)
            except Exception as error:
                codes = set(classify(error))
                if '1003' in codes and allow_day and 'day' not in recovered:
                    recovered.add('day')
                    initialize_day()
                elif '1002' in codes and 'master' not in recovered:
                    recovered.add('master')
                    update_master()
                elif codes & {'401', 'UNAUTHENTICATED'} and allow_auth and 'auth' not in recovered:
                    recovered.add('auth')
                    refresh_auth()
                else:
                    raise ShopReadError(codes) from None
    return read

def select(value, fields):
    return {key: value[key] for key in fields if key in value}

def collect_shop(fetch):
    directory = fetch('List', None)  # Failure is fatal: no invented empty directory.
    if not isinstance(directory.get('shops', []), list):
        raise CollectorError('Invalid Shop.List directory')
    result = {'schema': 1, 'scope': 'current_account_api_returned_directory', 'shops': [],
              'dokanInfos': [], 'banners': {}, 'complete': True}
    for field in ('bannerInfo', 'topBannerInfo'):
        if field in directory:
            result['banners'][field] = select(directory[field], ('assetId', 'linkDetail'))
    for value in directory.get('dokanInfos', []):
        row = select(value, ('id', 'name', 'description', 'linkTitle', 'linkType', 'linkDetail', 'assetId', 'shopType'))
        if 'shopItem' in value:
            row['shopItem'] = select(value['shopItem'], ITEM_FIELDS)
        result['dokanInfos'].append(row)
    seen = set()
    for position, shop in enumerate(directory.get('shops', [])):
        row = select(shop, SHOP_FIELDS)
        row.update(directory_index=position, items=[], requests=[])
        result['shops'].append(row)
        shop_id = shop.get('id')
        if not shop_id or shop_id in seen:
            row['status'] = 'invalid_or_duplicate_shop_id'
            result['complete'] = False
            continue
        seen.add(shop_id)
        methods = [('ListItem', 'shopItems')]
        kind = shop.get('type')
        if kind in (3, '3', 'ShopType_LoginBonusShop'):
            methods.append(('GetLoginBonusPackageItem', 'loginBonusPackageItem'))
        elif kind in (4, '4', 'ShopType_ConditionRewardShop'):
            methods.append(('GetConditionRewardPackageItem', 'conditionRewardPackageItem'))
        for method, field in methods:
            try:
                response = fetch(method, shop_id)
                if method == 'ListItem':
                    items = response.get(field, [])
                    if not isinstance(items, list):
                        raise ValueError('invalid items')
                else:
                    package = response.get(field)
                    if not isinstance(package, dict) or not isinstance(package.get('shopItem'), dict):
                        raise ValueError('missing package item')
                    items = [package['shopItem']]
                if any(not isinstance(item, dict) or not item.get('id') for item in items):
                    raise ValueError('invalid item id')
                row['items'].extend(dict(select(item, ITEM_FIELDS), source_method=method) for item in items)
                row['requests'].append({'method': method, 'status': 'success', 'returned_count': len(items)})
            except Exception as error:
                # No server payload, headers, exception text, or credentials in export.
                row['requests'].append({'method': method, 'status': 'failed', 'error_type': type(error).__name__, 'error_codes': getattr(error, 'safe_codes', [])})
                result['complete'] = False
        row['conflicts'] = []
        indexed = {}
        for item in row['items']:
            prior = indexed.setdefault(item['id'], {})
            for field in ITEM_FIELDS:
                if field in item and field in prior and prior[field] != item[field]:
                    row['conflicts'].append({'item_id': item['id'], 'field': field})
                if field in item:
                    prior[field] = item[field]
        if row['conflicts']:
            result['complete'] = False
        row['status'] = 'success' if not row['conflicts'] and all(r['status'] == 'success' for r in row['requests']) else 'failed'
        row['unique_item_count'] = len({item['id'] for item in row['items']})
    result['returned_shop_count'] = len(result['shops'])
    result['successful_shop_count'] = sum(row['status'] == 'success' for row in result['shops'])
    return result

def fetch_shop(solis_dir, app_version, account_path, *, allow_auth=False, initialize_day=False):
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.json_format import MessageToDict
    with account_lock(account_path):
        collector = OfficialCollector(solis_dir, app_version, account_path, create=False, initialize_day=initialize_day)
        try:
            metadata = collector.account.get('metadata')
            if not metadata or metadata.get('x-app-version') != app_version:
                if not allow_auth:
                    raise CollectorError('Matching cached authentication required; use --allow-auth-refresh explicitly')
                metadata = collector.authenticate()
            stub = collector.stubs.ShopStub(collector.channel)
            def raw_fetch(method, shop_id):
                if method not in METHODS:
                    raise CollectorError('Shop method not allowed')
                request = Empty() if method == 'List' else getattr(collector.api, 'Shop' + method + 'Request')(shopId=shop_id)
                response = collector.call(getattr(stub, method), request, metadata, read_only=True)
                return MessageToDict(response, preserving_proto_field_name=True)
            def classify(error):
                if not isinstance(error, collector.grpc.RpcError):
                    return []
                codes = {str(v) for k, v in [*(error.initial_metadata() or []), *(error.trailing_metadata() or [])] if k == 'x-error-code'}
                if error.code() == collector.grpc.StatusCode.UNAUTHENTICATED:
                    codes.add('UNAUTHENTICATED')
                return codes
            def refresh():
                nonlocal metadata
                metadata = collector.authenticate(force_refresh=True)
            fetch = recover_read(raw_fetch, classify, refresh_auth=refresh,
                                 update_master=lambda: collector.update_master(metadata),
                                 initialize_day=lambda: collector.initialize_day(metadata),
                                 allow_auth=allow_auth, allow_day=initialize_day)
            result = collect_shop(fetch)
            result['fetched_at'] = datetime.now(timezone.utc).isoformat()
            return result
        finally:
            collector.channel.close()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solis-dir', type=Path, required=True)
    parser.add_argument('--app-version', required=True)
    parser.add_argument('--account', type=Path, required=True)
    parser.add_argument('--allow-auth-refresh', action='store_true')
    parser.add_argument('--initialize-day', action='store_true')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'cache/shop/current.json')
    args = parser.parse_args(argv)
    try:
        result = fetch_shop(args.solis_dir, args.app_version, args.account,
                            allow_auth=args.allow_auth_refresh, initialize_day=args.initialize_day)
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'error_codes': getattr(error, 'safe_codes', [])}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from .master_fetch import atomic_json
    atomic_json(args.output, result)
    print(json.dumps({'shops': result['returned_shop_count'], 'successful_shops': result['successful_shop_count'], 'complete': result['complete']}))
    return 0 if result['complete'] else 1
