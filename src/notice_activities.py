"""Discover public notice links from current activities, never gameplay text."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .notice_fetch import HOST, notice_path, sanitize_notices


@dataclass(frozen=True)
class ActivityRoute:
    home_field: str
    service: str
    request_field: str | None = None
    unlock_field: str = "isUnlocked"


# Explicit read endpoints from the client protocol, not arbitrary RPC reflection.
# IDs come only from Home.Enter. No Start/ReadStory/Receive/Finish endpoint belongs here.
ROUTES = (
    ActivityRoute("tourInfos", "Tour", "tourId"),
    ActivityRoute("backsideInfos", "Backside", "backsideId"),
    ActivityRoute("marathonInfos", "Marathon", "marathonId"),
    ActivityRoute("photoContestInfos", "PhotoContest", "photoContestId"),
    ActivityRoute("raceInfos", "Race", "raceId"),
    ActivityRoute("loveEventInfos", "Love", "loveId"),
    ActivityRoute("dreamInfo", "Dream"),
    ActivityRoute("mosaicInfos", "Mosaic", "mosaicId", "unlocked"),
    ActivityRoute("diceInfo", "Dice"),
    ActivityRoute("shelfInfo", "Shelf"),
    ActivityRoute("dutyInfos", "Duty", "dutyId"),
)
MAX_ACTIVITIES = 100


def rpc_application_codes(error) -> set[str]:
    return {str(value) for key, value in
            [*(error.initial_metadata() or []), *(error.trailing_metadata() or [])]
            if key == "x-error-code" and re.fullmatch(r"[0-9]{1,5}", str(value))}


def public_notice(record: dict) -> dict | None:
    """Only official WebView pages and the existing static field allowlist escape."""
    clean = sanitize_notices([record])
    if not clean or not clean[0].get("linkDetail"):
        return None
    clean = clean[0]
    link = clean["linkDetail"]
    if not isinstance(link, str):
        raise ValueError("Activity notice link must be a string")
    # Game navigation and links to other services are not translatable notice pages.
    if not (link.startswith("https://" + HOST + "/") or link.startswith("/notice/")):
        return None
    path = notice_path(link)
    clean["linkDetail"] = "https://" + HOST + path
    if not clean.get("id"):
        clean["id"] = "page-" + hashlib.sha256(path.encode()).hexdigest()
    if not isinstance(clean["id"], str):
        raise ValueError("Activity notice ID must be a string")
    return clean


def extract_notices(message) -> list[dict]:
    """Traverse only schema branches capable of containing api.NoticeInfo.

    Never serialize the whole RPC response: it can contain inventory, player
    data and random short stories. commonResponse is always outside this input.
    """
    from google.protobuf.json_format import MessageToDict
    from google.protobuf.descriptor import FieldDescriptor

    def reachable(descriptor, seen):
        if descriptor.full_name == "api.NoticeInfo":
            return True
        if descriptor.full_name in seen or len(seen) > 32:
            return False
        seen = seen | {descriptor.full_name}
        return any(field.message_type and field.name != "commonResponse" and
                   reachable(field.message_type, seen) for field in descriptor.fields)

    result = []
    visited = 0

    def visit(node, depth=0):
        nonlocal visited
        visited += 1
        if depth > 32 or visited > 10000:
            raise ValueError("Activity notice response exceeds traversal limits")
        if node.DESCRIPTOR.full_name == "api.NoticeInfo":
            notice = public_notice(MessageToDict(node, preserving_proto_field_name=True))
            if notice:
                result.append(notice)
            return
        for field, value in node.ListFields():
            if not field.message_type or field.name == "commonResponse" or not reachable(field.message_type, set()):
                continue
            if field.label == FieldDescriptor.LABEL_REPEATED:
                for child in value:
                    visit(child, depth + 1)
            else:
                visit(value, depth + 1)

    visit(message)
    return result


def discover_activity_notices(collector) -> tuple[list[dict], dict]:
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.descriptor import FieldDescriptor

    def read(service, method, request):
        stub = getattr(collector.stubs, service + "Stub")(collector.channel)
        return collector.call(getattr(stub, method), request,
                              collector.account["metadata"], read_only=True)

    home = read("Home", "Enter", collector.api.HomeEnterRequest(skipUpdate=True))
    notices = extract_notices(home)
    statuses = []
    events = home.eventInfo
    for route in ROUTES:
        field = events.DESCRIPTOR.fields_by_name.get(route.home_field)
        if field is None:
            raise ValueError("Activity protocol is missing a supported Home field")
        if field.label == FieldDescriptor.LABEL_REPEATED:
            entries = getattr(events, route.home_field)
        else:
            entries = [getattr(events, route.home_field)] if events.HasField(route.home_field) else []
        for event in entries:
            if len(statuses) >= MAX_ACTIVITIES:
                raise ValueError("Too many current activities")
            status = {"service": route.service + ".Top"}
            if "id" in event.DESCRIPTOR.fields_by_name:
                event_id = event.id
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
                    raise ValueError("Invalid activity ID in Home response")
                status["event_id"] = event_id
            if route.unlock_field not in event.DESCRIPTOR.fields_by_name:
                raise ValueError("Activity protocol is missing an unlock indicator")
            if not getattr(event, route.unlock_field):
                statuses.append({**status, "status": "locked", "notices": 0})
                continue
            request = (getattr(collector.api, route.service + "TopRequest")(
                **{route.request_field: status["event_id"]}) if route.request_field else Empty())
            try:
                response = read(route.service, "Top", request)
            except collector.grpc.RpcError as error:
                codes = rpc_application_codes(error)
                if codes == {"3010"} or codes == {"1005"}:
                    statuses.append({**status, "status": "condition_unmet" if "3010" in codes else "maintenance",
                                     "codes": sorted(codes), "notices": 0})
                    continue
                # Authentication, stale MasterDB, date change and transport errors
                # belong to the existing recovery/failure path, never a silent skip.
                raise
            found = extract_notices(response)
            notices.extend(found)
            statuses.append({**status, "status": "ok" if found else "no_notice", "notices": len(found)})

    # General events can point directly to an official page rather than NoticeInfo.
    for event in events.generalEventInfos:
        notice = public_notice({"id": event.id, "title": event.text, "linkDetail": event.linkDetail})
        if notice:
            notices.append(notice)
    by_id = {}
    for notice in notices:
        old = by_id.get(notice["id"])
        if old is not None and old != notice:
            raise ValueError("Conflicting activity notice metadata for the same ID")
        by_id[notice["id"]] = notice
    records = [by_id[key] for key in sorted(by_id)]
    incomplete = [entry for entry in statuses if entry["status"] != "ok"]
    return records, {"schema_version": 1, "scope": "current_home_activities_and_embedded_notices",
                     "complete_for_supported_activities": not incomplete,
                     "supported_services": [route.service + ".Top" for route in ROUTES],
                     "activities": statuses, "notice_records": len(records),
                     "unique_pages": len({record["linkDetail"] for record in records})}
