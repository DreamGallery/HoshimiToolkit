"""Collect current account API-returned Exchange static catalog."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from .notice_official import OfficialCollector, CollectorError, account_lock
from .shop_fetch import select, recover_read

BOOTH_FIELDS = ('id', 'name', 'bannerAssetId', 'requiredResourceType', 'requiredResourceId', 'order', 'costumeId', 'iconAssetId')
ITEM_FIELDS = ('id', 'name', 'description', 'assetId', 'rewardId', 'requiredResourceAmount', 'order')

def collect_exchange(fetch):
    response = fetch('List', None)
    booths = response.get('booths', [])
    if not isinstance(booths, list):
        raise CollectorError('Invalid Exchange directory')
    result = {'schema': 1, 'scope': 'current_account_api_returned_directory', 'booths': [], 'complete': True, 'conflicts': []}
    seen = {}
    items_seen = {}
    for position, booth in enumerate(booths):
        if not isinstance(booth, dict):
            raise CollectorError('Invalid Exchange booth')
        row = dict(select(booth, BOOTH_FIELDS), directory_index=position, exchanges=[], status='success')
        result['booths'].append(row)
        bid = booth.get('id')
        if not bid:
            row['status'] = 'invalid_booth_id'
            result['complete'] = False
        elif bid in seen:
            result['conflicts'].append({'booth_id': bid, 'field': 'duplicate_booth_id'})
            row['status'] = 'failed'
            result['complete'] = False
        seen[bid] = row
        entries = booth.get('exchanges', [])
        if not isinstance(entries, list):
            row['status'] = 'invalid_exchanges'
            result['complete'] = False
            continue
        for item in entries:
            if not isinstance(item, dict) or not item.get('id'):
                row['status'] = 'invalid_exchange_id'
                result['complete'] = False
                continue
            value = select(item, ITEM_FIELDS)
            row['exchanges'].append(value)
            prior = items_seen.setdefault(item['id'], {})
            for field in ITEM_FIELDS:
                if field in prior and field in value and prior[field] != value[field]:
                    result['conflicts'].append({'booth_id': bid, 'exchange_id': item['id'], 'field': field})
                    row['status'] = 'failed'
                    result['complete'] = False
                if field in value:
                    prior[field] = value[field]
        row['returned_exchange_count'] = len(entries)
        row['unique_exchange_count'] = len({item['id'] for item in row['exchanges']})
    result['returned_booth_count'] = len(booths)
    result['successful_booth_count'] = sum(row['status'] == 'success' for row in result['booths'])
    result['unique_exchange_count'] = len(items_seen)
    result['requests'] = [{'method': 'List', 'status': 'success', 'returned_count': len(booths)}]
    return result

def fetch_exchange(solis_dir, app_version, account_path, *, allow_auth=False, initialize_day=False):
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.json_format import MessageToDict
    with account_lock(account_path):
        collector = OfficialCollector(solis_dir, app_version, account_path, create=False, initialize_day=initialize_day)
        try:
            metadata = collector.account.get('metadata')
            if not metadata or metadata.get('x-app-version') != app_version:
                if not allow_auth:
                    raise CollectorError('Matching cached authentication required')
                metadata = collector.authenticate()
            stub = collector.stubs.ExchangeStub(collector.channel)
            def raw_fetch(method, unused):
                if method != 'List':
                    raise CollectorError('Exchange method not allowed')
                response = collector.call(stub.List, Empty(), metadata, read_only=True)
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
            fetch = recover_read(raw_fetch, classify, refresh_auth=refresh, update_master=lambda: collector.update_master(metadata), initialize_day=lambda: collector.initialize_day(metadata), allow_auth=allow_auth, allow_day=initialize_day)
            result = collect_exchange(fetch)
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
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'cache/exchange/current.json')
    args = parser.parse_args(argv)
    try:
        result = fetch_exchange(args.solis_dir, args.app_version, args.account, allow_auth=args.allow_auth_refresh, initialize_day=args.initialize_day)
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'error_codes': getattr(error, 'safe_codes', [])}))
        return 1
    from .master_fetch import atomic_json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps({'booths': result['returned_booth_count'], 'items': result['unique_exchange_count'], 'complete': result['complete']}))
    return 0 if result['complete'] else 1
