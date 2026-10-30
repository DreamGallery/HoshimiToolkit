import json
from types import SimpleNamespace
import unittest

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from src.notice_activities import ROUTES, discover_activity_notices, extract_notices, public_notice
from src.notice_fetch import collect_entry_titles, input_paths


PAGE = '/notice/' + 'a' * 64 + '/index.html'
URL = 'https://stat.game-idolypride.jp' + PAGE


def messages():
    """Minimal wire types; no live account or generated game SDK in the suite."""
    file = descriptor_pb2.FileDescriptorProto(name='notice_test.proto', package='api', syntax='proto3')

    def message(name, fields):
        result = file.message_type.add(name=name)
        for number, (name, kind, repeated) in enumerate(fields, 1):
            field = result.field.add(name=name, number=number,
                                     label=3 if repeated else 1,
                                     type={'string': 9, 'bool': 8}.get(kind, 11))
            if field.type == 11:
                field.type_name = '.api.' + kind

    message('NoticeInfo', [(x, 'string', False) for x in ('id', 'title', 'listTitle', 'linkDetail', 'associateToken')])
    message('Story', [('text', 'string', False), ('name', 'string', False)])
    message('Info', [('noticeInfo', 'NoticeInfo', False)])
    event_fields = []
    for route in ROUTES:
        name = 'Home' + route.service
        message(name, [('id', 'string', False), (route.unlock_field, 'bool', False)])
        event_fields.append((route.home_field, name, route.home_field.endswith('Infos')))
    message('General', [('id', 'string', False), ('text', 'string', False), ('linkDetail', 'string', False)])
    message('Events', event_fields + [('generalEventInfos', 'General', True)])
    message('Response', [('eventInfo', 'Events', False), ('info', 'Info', False),
                         ('dokanInfos', 'Info', True), ('userStory', 'Story', False),
                         ('commonResponse', 'Info', False)])
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return {name: message_factory.GetMessageClass(pool.FindMessageTypeByName('api.' + name))
            for name in ('Response', 'NoticeInfo')}


class RpcError(Exception):
    def __init__(self, code): self.application_code = code
    def initial_metadata(self): return [('x-auth-token', 'private'), ('x-error-code', self.application_code)]
    def trailing_metadata(self): return []


class ActivityNoticeTests(unittest.TestCase):
    def setUp(self):
        self.types = messages()
        self.home = self.types['Response']()
        self.response = self.types['Response']()
        self.response.info.noticeInfo.CopyFrom(self.notice('activity'))
        self.calls = []
        self.failures = {}
        api = SimpleNamespace(HomeEnterRequest=lambda **args: args)
        stubs = SimpleNamespace(HomeStub=lambda _: SimpleNamespace(Enter=('Home', 'Enter')))
        for route in ROUTES:
            setattr(stubs, route.service + 'Stub', lambda _, service=route.service: SimpleNamespace(Top=(service, 'Top')))
            setattr(api, route.service + 'TopRequest', lambda **args: args)
        def call(method, request, metadata, *, read_only):
            self.assertTrue(read_only)
            self.calls.append((method, request))
            if method[0] in self.failures:
                raise self.failures[method[0]]
            return self.home if method == ('Home', 'Enter') else self.response
        self.collector = SimpleNamespace(api=api, stubs=stubs, channel=object(),
                                        account={'metadata': {'x-auth-token': 'fixture'}},
                                        grpc=SimpleNamespace(RpcError=RpcError), call=call)

    def notice(self, identity):
        return self.types['NoticeInfo'](id=identity, title='玩法说明', listTitle='活动入口',
                                        linkDetail=URL, associateToken='must-not-export')

    def event(self, route, unlocked=True):
        field = getattr(self.home.eventInfo, route.home_field)
        event = field.add() if route.home_field.endswith('Infos') else field
        event.id = route.service.lower() + '-event'
        setattr(event, route.unlock_field, unlocked)
        return event

    def test_all_supported_routes_use_current_ids_and_read_calls_only(self):
        for route in ROUTES:
            self.event(route)
        records, report = discover_activity_notices(self.collector)
        self.assertEqual(self.calls[0], (('Home', 'Enter'), {'skipUpdate': True}))
        self.assertEqual(len(self.calls), len(ROUTES) + 1)
        for route, (method, request) in zip(ROUTES, self.calls[1:]):
            self.assertEqual(method, (route.service, 'Top'))
            if route.request_field:
                self.assertEqual(request, {route.request_field: route.service.lower() + '-event'})
        self.assertTrue(report['complete_for_supported_activities'])
        self.assertEqual(len(records), 1)  # repeated placements of one notice

    def test_only_notice_info_leaves_response_and_common_response_is_excluded(self):
        self.response.userStory.text = 'private short story, do not translate'
        self.response.userStory.name = 'player nickname'
        self.response.commonResponse.noticeInfo.CopyFrom(self.notice('private'))
        records = extract_notices(self.response)
        self.assertEqual(len(records), 1)
        encoded = json.dumps(records)
        for private in ('private', 'nickname', 'must-not-export', 'associateToken'):
            self.assertNotIn(private, encoded)

    def test_locked_activity_is_reported_without_calling_top(self):
        self.event(next(r for r in ROUTES if r.service == 'Dice'), False)
        records, report = discover_activity_notices(self.collector)
        self.assertFalse(report['complete_for_supported_activities'])
        self.assertEqual(report['activities'][0]['status'], 'locked')
        self.assertEqual([x[0] for x in self.calls], [('Home', 'Enter')])
        self.assertEqual(records, [])

    def test_expected_denial_is_partial_but_network_auth_and_schema_errors_fail(self):
        self.event(next(r for r in ROUTES if r.service == 'Dice'))
        for code, status in [('3010', 'condition_unmet'), ('1005', 'maintenance')]:
            self.failures['Dice'] = RpcError(code)
            _, report = discover_activity_notices(self.collector)
            self.assertEqual(report['activities'][0]['status'], status)
            self.assertFalse(report['complete_for_supported_activities'])
        for code in ('1002', '1003', '2004', '2002', '3011', '3006', ''):
            self.failures['Dice'] = RpcError(code)
            with self.assertRaises(RpcError): discover_activity_notices(self.collector)

    def test_general_event_links_and_duplicate_fetch_paths(self):
        self.home.info.noticeInfo.CopyFrom(self.notice('home'))
        row = self.home.eventInfo.generalEventInfos.add(id='general', text='活动规则', linkDetail=URL)
        records, _ = discover_activity_notices(self.collector)
        index = {'notices': [{'id': 'normal', 'linkDetail': URL}], 'activityNotices': records}
        self.assertEqual(input_paths(index), [PAGE])
        self.assertEqual(len(collect_entry_titles(index)['entries']), 3)
        self.assertEqual(row.linkDetail, URL)  # source is never mutated

    def test_conflicting_notice_identity_stops_instead_of_selecting_by_order(self):
        self.response.dokanInfos.add().noticeInfo.CopyFrom(self.notice('activity'))
        self.response.dokanInfos[0].noticeInfo.title = 'different title'
        self.event(ROUTES[0])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            discover_activity_notices(self.collector)

    def test_url_validation_and_stable_missing_id(self):
        notice = public_notice({'linkDetail': URL + '?_v=20261031-000000', 'title': '规则'})
        self.assertEqual(notice['linkDetail'], URL)
        self.assertEqual(notice['id'], public_notice({'linkDetail': URL})['id'])
        self.assertIsNone(public_notice({'linkDetail': 'https://other.example/notice/x'}))
        self.assertIsNone(public_notice({'linkDetail': 'game.navigation'}))
        for link in (URL + '?token=private', URL + '#private', '/notice/../secret'):
            with self.assertRaises(ValueError): public_notice({'linkDetail': link})


if __name__ == '__main__': unittest.main()
