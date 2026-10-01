"""Dedicated notice collector authentication; never runs SolisClient's login routine."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import time

from .master_fetch import atomic_json, firebase_api_key
from .notice_fetch import CATEGORIES, ROOT, select_notice_scope


class CollectorError(RuntimeError):
    """Fixed, credential-free diagnostic suitable for CLI output."""


def validate_account_path(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to((ROOT / "cache").resolve()):
        raise CollectorError("Collector account must stay in ignored Toolkit/cache")
    return path


def save_account(path: Path, account: dict) -> None:
    validate_account_path(path)
    atomic_json(path, account)  # tempfile is created with mode 0600
    path.chmod(0o600)


@contextmanager
def account_lock(path: Path):
    path = validate_account_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CollectorError("Collector account is locked; inspect the previous process before removing its lock") from None
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink()


def load_account(path: Path, create: bool) -> dict:
    validate_account_path(path)
    if not path.exists():
        if not create:
            raise CollectorError("No collector account; initial creation requires --create-account")
        return {"schema": 1, "purpose": "dedicated_notice_collector"}
    account = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(account, dict) or account.get("schema") != 1 or account.get("purpose") != "dedicated_notice_collector":
        raise CollectorError("Invalid dedicated collector account cache")
    if account.get("pending_create"):
        raise CollectorError("Previous account creation has uncertain outcome; inspect it instead of creating another account")
    path.chmod(0o600)
    return account


def update_firebase_tokens(account: dict, response: dict, *, refresh: bool = False) -> None:
    fields = ("id_token", "refresh_token", "expires_in") if refresh else ("idToken", "refreshToken", "expiresIn")
    token, renewable, lifetime = (response.get(field) for field in fields)
    if not isinstance(token, str) or not token or not isinstance(renewable, str) or not renewable:
        raise CollectorError("Firebase response is missing tokens")
    try:
        lifetime = int(lifetime)
    except (TypeError, ValueError):
        raise CollectorError("Firebase response has invalid expiry") from None
    if lifetime <= 0:
        raise CollectorError("Firebase response has invalid expiry")
    account.update(firebase_id_token=token, firebase_refresh_token=renewable, expires_at=time.time() + lifetime)
    uid = response.get("user_id" if refresh else "localId")
    if uid:
        account["firebase_uid"] = uid


class OfficialCollector:
    def __init__(self, solis_dir: Path, app_version: str, account_path: Path, create: bool = False, initialize_day: bool = False):
        import sys
        sys.path.insert(0, str(solis_dir.resolve()))
        self.grpc = importlib.import_module("grpc")
        self.requests = importlib.import_module("requests")
        self.api = importlib.import_module("papi_pb2")
        self.stubs = importlib.import_module("papi_grpc")
        self.config = json.loads((solis_dir / "client_config.json").read_text(encoding="utf-8"))
        self.app_version = app_version
        if not app_version or any(c in app_version for c in "\r\n"):
            raise CollectorError("Explicit current app version is required")
        self.path = validate_account_path(account_path)
        self.allow_create = create
        self.allow_initialize = initialize_day
        self.account = load_account(self.path, create)
        roots = (solis_dir / "iprroots.pem").read_bytes()
        self.channel = self.grpc.secure_channel("api.game-idolypride.jp:443",
            self.grpc.ssl_channel_credentials(root_certificates=roots),
            options=[("grpc.primary_user_agent", "grpc-csharp/2.37.0-dev grpc-c/15.0.0 (android; chttp2)")])

    def save(self):
        save_account(self.path, self.account)

    def firebase(self, operation: str, body: dict) -> dict:
        endpoints = {
            "signup": "https://identitytoolkit.googleapis.com/v1/accounts:signUp",
            "refresh": "https://securetoken.googleapis.com/v1/token",
        }
        headers = dict(self.config["firebase"])
        kwargs = {"json": body}
        if operation == "refresh":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs = {"data": body}
        # Mutating signup/refresh calls are never blindly retried.
        try:
            response = self.requests.post(endpoints[operation], params={"key": firebase_api_key()},
                                          headers=headers, timeout=30, allow_redirects=False, **kwargs)
        except self.requests.RequestException:
            raise CollectorError("Firebase " + operation + " transport failed; no automatic retry") from None
        if response.status_code != 200:
            raise CollectorError("Firebase " + operation + " rejected (HTTP " + str(response.status_code) + ")")
        return response.json()

    def call(self, method, request, metadata: dict, *, read_only=False):
        attempts = 3 if read_only else 1
        for attempt in range(attempts):
            try:
                response, context = method.with_call(request, metadata=list(metadata.items()), timeout=30)
                for key, value in [*(context.initial_metadata() or []), *(context.trailing_metadata() or [])]:
                    if key == "x-error-code" and str(value) not in ("", "0"):
                        raise CollectorError("Official API returned an application error; no state-changing fallback")
                return response
            except self.grpc.RpcError as error:
                if error.code() not in (self.grpc.StatusCode.UNAVAILABLE, self.grpc.StatusCode.DEADLINE_EXCEEDED) or attempt + 1 == attempts:
                    raise
                time.sleep(attempt + 1)

    def authenticate(self, *, force_refresh=False) -> dict:
        if not self.account.get("firebase_refresh_token"):
            if not self.allow_create:
                raise CollectorError("Creating a Firebase collector requires --create-account")
            self.account["pending_create"] = "firebase_signup"
            self.save()
            response = self.firebase("signup", {"returnSecureToken": True})
            update_firebase_tokens(self.account, response)
            self.account.pop("pending_create")
            self.save()
        elif force_refresh or self.account.get("expires_at", 0) <= time.time() + 60:
            response = self.firebase("refresh", {"grant_type": "refresh_token", "refresh_token": self.account["firebase_refresh_token"]})
            update_firebase_tokens(self.account, response, refresh=True)
            self.save()
        headers = dict(self.config["qseed"])
        headers["X-AppVersion"] = self.app_version
        try:
            seed = self.requests.get("https://id.qseed.jp/getId", headers=headers, timeout=30, allow_redirects=False)
        except self.requests.RequestException:
            raise CollectorError("Official seed transport failed") from None
        if seed.status_code != 200 or not seed.text or len(seed.text) > 4096:
            raise CollectorError("Official seed request failed")
        metadata = dict(self.config["grpc"])
        metadata.pop("x-auth-token", None)
        metadata.update({"x-app-version": self.app_version, "x-seed-id": seed.text})
        system = self.stubs.SystemStub(self.channel)
        self.call(system.Check, self.api.SystemCheckRequest(), {"x-app-version": self.app_version, "content-type": metadata["content-type"]}, read_only=True)
        self.call(system.Check, self.api.SystemCheckRequest(firebaseIDToken=self.account["firebase_id_token"]), metadata, read_only=True)
        auth = self.stubs.AuthStub(self.channel)
        login = self.call(auth.Login, self.api.AuthLoginRequest(firebaseIDToken=self.account["firebase_id_token"]), metadata)
        if login.requiredFirebaseReauthenticate:
            # A newly anonymous Firebase identity may need its updated claims.
            # Refresh once; never guess Auth.Create/custom-token side effects.
            response = self.firebase("refresh", {"grant_type": "refresh_token", "refresh_token": self.account["firebase_refresh_token"]})
            update_firebase_tokens(self.account, response, refresh=True)
            self.save()
            login = self.call(auth.Login, self.api.AuthLoginRequest(firebaseIDToken=self.account["firebase_id_token"]), metadata)
        if login.requiredFirebaseReauthenticate or not login.gameAuthToken:
            raise CollectorError("Official authentication requires additional handling; no gameplay fallback")
        metadata["x-auth-token"] = login.gameAuthToken
        self.account["game_registered"] = True
        self.account["metadata"] = metadata
        self.save()
        self.update_master(metadata)
        return metadata

    def update_master(self, metadata):
        from google.protobuf.empty_pb2 import Empty
        result = self.call(self.stubs.MasterStub(self.channel).Get, Empty(), metadata, read_only=True)
        if not result.masterTag.version:
            raise CollectorError("Official Master.Get did not return a version")
        metadata["x-master-version"] = result.masterTag.version
        self.account["metadata"] = metadata
        self.save()

    def initialize_day(self, metadata):
        if not self.allow_initialize:
            raise CollectorError("Daily initialization requires --initialize-day")
        # Only the dedicated collector is accepted by load_account. Home.Login
        # may grant automatic login rewards and applies these collector settings.
        settings = self.api.SettingInfo(soundBgm=50, soundEffect=50, soundVoice=50,
            graphicType=1, frameRate=30, activityFinishNotification=False,
            messageNotification=False, nightMode=True, notLoginNotification=False)
        headers = dict(metadata)
        headers["x-app-request-id"] = str(int(time.time() * 10000000 + 621355968000000000))
        self.call(self.stubs.HomeStub(self.channel).Login,
                  self.api.HomeLoginRequest(settingInfo=settings), headers)
        self.account["last_daily_initialization"] = datetime.now(timezone.utc).isoformat()
        self.save()

    def fetch(self, max_pages: int, *, archive: bool = False) -> dict:
        from google.protobuf.empty_pb2 import Empty
        from google.protobuf.json_format import MessageToDict
        if max_pages < 1:
            raise CollectorError("max-pages must be positive")
        metadata = self.account.get("metadata")
        if not metadata or metadata.get("x-app-version") != self.app_version:
            metadata = self.authenticate()
        elif not metadata.get("x-master-version"):
            self.update_master(metadata)
        stub = self.stubs.NoticeStub(self.channel)
        def as_dict(method, request):
            return MessageToDict(self.call(method, request, metadata, read_only=True), preserving_proto_field_name=True)
        recovered = set()
        for _ in range(4):
            try:
                result = select_notice_scope(as_dict(stub.List, Empty()),
                    lambda category, offset: as_dict(stub.FetchList, self.api.NoticeFetchRequest(noticeCategoryType=category, offset=offset)), max_pages, archive=archive)
                result["_provenance"] = {"source": "https://api.game-idolypride.jp/api.Notice/List",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "returned_per_category": {field: len(result[field]) for _, field, _ in CATEGORIES},
                    "completeness": "all_pages_returned_by_official_api_for_collector" if archive else "current_list_first_batches_not_exhaustive"}
                return result
            except self.grpc.RpcError as error:
                codes = {str(v) for k, v in [*(error.initial_metadata() or []), *(error.trailing_metadata() or [])] if k == "x-error-code"}
                if "1003" in codes:
                    if self.allow_initialize and "day" not in recovered:
                        self.initialize_day(metadata)
                        recovered.add("day")
                        continue
                    raise CollectorError("Official Notice API returned DateChanged (1003); stopped before Home.Login or reward-affecting calls") from None
                if "1002" in codes and "master" not in recovered:
                    self.update_master(metadata)
                    recovered.add("master")
                    continue
                if error.code() != self.grpc.StatusCode.UNAUTHENTICATED or "auth" in recovered:
                    raise
                metadata = self.authenticate(force_refresh=True)
                recovered.add("auth")
        raise CollectorError("Official notice recovery limit reached")


def fetch_official_index(solis_dir: Path, app_version: str, account_path: Path,
                         create: bool = False, max_pages: int = 1000, initialize_day: bool = False, *, archive: bool = False) -> dict:
    with account_lock(account_path):
        collector = OfficialCollector(solis_dir, app_version, account_path, create, initialize_day)
        try:
            return collector.fetch(max_pages, archive=archive)
        finally:
            collector.channel.close()
