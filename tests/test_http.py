"""`UrllibHttp` (task 9) against a fake opener: no socket is ever opened."""

import http.client
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pytest

from rushes.errors import SourceUnavailable
from rushes.net.base import HttpError
from rushes.net.http import UrllibHttp, build_opener


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        data: bytes = b"",
        status: int = 200,
        charset: str | None = None,
        length: int | None = None,
    ) -> None:
        super().__init__(data)
        self.status = status
        self.headers = Message()
        if length is not None:
            self.headers["Content-Length"] = str(length)
        if charset:
            self.headers["Content-Type"] = f"text/plain; charset={charset}"


class FakeOpener:
    """Callable `(request, timeout=...)` returning what the test configured."""

    def __init__(self, response: FakeResponse | Callable[[], FakeResponse] | Exception) -> None:
        self.response = response
        self.requests: list[Any] = []
        self.timeouts: list[float] = []

    def __call__(self, request: Any, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response() if callable(self.response) else self.response


def agent() -> str:
    try:
        return f"rushes/{version('rushes')}"
    except PackageNotFoundError:
        return "rushes/dev"


def test_http_sends_the_user_agent_and_the_timeout() -> None:
    opener = FakeOpener(FakeResponse(b"hello"))
    assert UrllibHttp(opener=opener).get_text("https://example.test/x") == "hello"
    (request,) = opener.requests
    assert request.get_header("User-agent") == agent()
    assert opener.timeouts == [30.0]
    assert request.get_header("Cookie") is None  # no cookie, ever


def test_http_timeout_is_configurable() -> None:
    opener = FakeOpener(FakeResponse(b"{}"))
    UrllibHttp(timeout=5, opener=opener).get_json("https://example.test/x")
    assert opener.timeouts == [5.0]


def test_http_get_json_encodes_params() -> None:
    opener = FakeOpener(lambda: FakeResponse(json.dumps({"a": 1}).encode()))
    http = UrllibHttp(opener=opener)
    assert http.get_json("https://example.test/api", params={"q": "a b", "n": "1"}) == {"a": 1}
    assert opener.requests[0].full_url == "https://example.test/api?q=a+b&n=1"
    http.get_json("https://example.test/api?x=1", params={"q": "z"})
    assert opener.requests[1].full_url == "https://example.test/api?x=1&q=z"
    assert opener.requests[0].get_header("Accept") == "application/json"


def test_http_get_json_on_invalid_json_raises_http_error() -> None:
    http = UrllibHttp(opener=FakeOpener(FakeResponse(b"<html>")))
    with pytest.raises(HttpError, match="JSON"):
        http.get_json("https://example.test/x")


def test_http_get_text_uses_the_declared_charset() -> None:
    opener = FakeOpener(FakeResponse("é".encode("latin-1"), charset="latin-1"))
    assert UrllibHttp(opener=opener).get_text("https://example.test/x") == "é"


def test_http_post_returns_the_status_and_sends_body_and_headers() -> None:
    opener = FakeOpener(FakeResponse(b"ok", status=200))
    status = UrllibHttp(opener=opener).post(
        "https://ntfy.test/topic", body=b"payload", headers={"Title": "t"}
    )
    assert status == 200
    (request,) = opener.requests
    assert request.get_method() == "POST"
    assert request.data == b"payload"
    assert request.get_header("Title") == "t"
    assert request.get_header("User-agent") == agent()


def test_r47_http_error_status_becomes_http_error_without_secrets() -> None:
    error = urllib.error.HTTPError(
        "https://ntfy.test/secret-topic", 403, "Forbidden", Message(), io.BytesIO(b"token=abc")
    )
    http = UrllibHttp(opener=FakeOpener(error))
    with pytest.raises(HttpError) as excinfo:
        http.post(
            "https://ntfy.test/secret-topic",
            body=b"BODY-SECRET",
            headers={"Authorization": "Bearer s3cr3t"},
        )
    text = f"{excinfo.value} {excinfo.value.hint}"
    assert excinfo.value.status == 403
    assert isinstance(excinfo.value, SourceUnavailable)
    for secret in ("secret-topic", "BODY-SECRET", "s3cr3t", "token=abc"):
        assert secret not in text


def test_http_non_2xx_response_is_an_error() -> None:
    http = UrllibHttp(opener=FakeOpener(FakeResponse(b"", status=302)))
    with pytest.raises(HttpError) as excinfo:
        http.get_text("https://example.test/x")
    assert excinfo.value.status == 302


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("unreachable"),
        TimeoutError("timed out"),
        ConnectionResetError("reset"),
    ],
    ids=["urlerror", "timeout", "reset"],
)
def test_http_network_errors_have_no_status(failure: Exception) -> None:
    http = UrllibHttp(opener=FakeOpener(failure))
    with pytest.raises(HttpError) as excinfo:
        http.get_text("https://example.test/x")
    assert excinfo.value.status is None
    assert "example.test" in str(excinfo.value)


def test_http_download_streams_to_part_then_renames(tmp_path: Path) -> None:
    payload = b"x" * (3 * (1 << 20) + 17)
    opener = FakeOpener(lambda: FakeResponse(payload))
    dest = tmp_path / "sub" / "model.bin"
    UrllibHttp(opener=opener).download("https://example.test/model.bin", dest)
    assert dest.read_bytes() == payload
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model.bin"]  # no .part left


def test_http_download_failure_removes_the_part_and_keeps_the_old_file(tmp_path: Path) -> None:
    class Breaking(FakeResponse):
        def __init__(self) -> None:
            super().__init__(b"y" * 100)
            self.calls = 0

        def read(self, size: int | None = -1) -> bytes:
            self.calls += 1
            if self.calls > 1:
                raise ConnectionResetError("cut")
            return super().read(10)

    dest = tmp_path / "model.bin"
    dest.write_bytes(b"old")
    with pytest.raises(HttpError):
        UrllibHttp(opener=FakeOpener(Breaking)).download("https://example.test/m", dest)
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["model.bin"]


def test_http_download_http_error_leaves_nothing(tmp_path: Path) -> None:
    error = urllib.error.HTTPError("https://example.test/m", 404, "Not Found", Message(), None)
    dest = tmp_path / "model.bin"
    with pytest.raises(HttpError) as excinfo:
        UrllibHttp(opener=FakeOpener(error)).download("https://example.test/m", dest)
    assert excinfo.value.status == 404
    assert list(tmp_path.iterdir()) == []


def test_http_download_does_not_mask_local_write_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    http = UrllibHttp(opener=FakeOpener(FakeResponse(b"data")))
    with pytest.raises(OSError) as excinfo:  # the parent "folder" is a file
        http.download("https://example.test/m", blocker / "model.bin")
    assert not isinstance(excinfo.value, HttpError)


def test_the_default_opener_opens_no_connection_when_built() -> None:
    http = UrllibHttp()  # the autouse fixture blocks sockets: building must not touch the network
    assert http.timeout == 30.0


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.test/x", "/etc/passwd"])
def test_http_refuses_schemes_other_than_http(url: str) -> None:
    opener = FakeOpener(FakeResponse(b"data"))
    with pytest.raises(HttpError, match="non prise en charge"):
        UrllibHttp(opener=opener).get_text(url)
    assert opener.requests == []


# --- a real HTTPResponse over a fake socket (I1) ---------------------------------------------


class _Socket:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def makefile(self, *args: Any, **kwargs: Any) -> io.BytesIO:
        return io.BytesIO(self._raw)


def real_response(raw: bytes) -> http.client.HTTPResponse:
    response = http.client.HTTPResponse(_Socket(raw))  # type: ignore[arg-type]
    response.begin()
    return response


CHUNKED_CUT = (
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    b"a\r\n0123456789\r\n14\r\nshort"  # the second chunk announces 20 bytes, 5 arrive
)
LENGTH_CUT = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n" + b"x" * 10


def test_http_cut_chunked_stream_is_an_http_error_for_every_method(tmp_path: Path) -> None:
    def http_for() -> UrllibHttp:
        return UrllibHttp(opener=FakeOpener(lambda: real_response(CHUNKED_CUT)))  # type: ignore[arg-type]

    calls = (
        lambda: http_for().get_text("https://example.test/x"),
        lambda: http_for().get_json("https://example.test/x"),
        lambda: http_for().post("https://example.test/x", body=b"b", headers={}),
        lambda: http_for().download("https://example.test/x", tmp_path / "m.bin"),
    )
    for call in calls:
        with pytest.raises(HttpError) as excinfo:
            call()
        assert excinfo.value.status is None
        assert "example.test" in str(excinfo.value) and "IncompleteRead" not in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []  # no .part left


def test_http_truncated_content_length_is_not_renamed_onto_dest(tmp_path: Path) -> None:
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"old")
    http = UrllibHttp(opener=FakeOpener(lambda: real_response(LENGTH_CUT)))  # type: ignore[arg-type]
    with pytest.raises(HttpError, match=r"téléchargement incomplet depuis example\.test"):
        http.download("https://example.test/m", dest)
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["m.bin"]


def test_http_download_with_matching_content_length_succeeds(tmp_path: Path) -> None:
    opener = FakeOpener(lambda: FakeResponse(b"x" * 10, length=10))
    UrllibHttp(opener=opener).download("https://example.test/m", tmp_path / "m.bin")
    assert (tmp_path / "m.bin").read_bytes() == b"x" * 10


def test_http_download_stops_beyond_max_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "m.bin"
    dest.write_bytes(b"old")
    opener = FakeOpener(lambda: FakeResponse(b"x" * 11))
    with pytest.raises(HttpError, match=r"téléchargement trop long depuis example\.test"):
        UrllibHttp(opener=opener).download("https://example.test/m", dest, max_bytes=10)
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["m.bin"]  # no .part


def test_http_download_accepts_exactly_max_bytes(tmp_path: Path) -> None:
    opener = FakeOpener(lambda: FakeResponse(b"x" * 10))
    UrllibHttp(opener=opener).download("https://example.test/m", tmp_path / "m.bin", max_bytes=10)
    assert (tmp_path / "m.bin").read_bytes() == b"x" * 10


def test_http_download_stops_reading_an_endless_stream(tmp_path: Path) -> None:
    class Endless(FakeResponse):
        reads = 0

        def read(self, size: int | None = -1) -> bytes:
            Endless.reads += 1
            if Endless.reads > 50:
                raise AssertionError("the stream was read past the limit")
            return b"x" * (1 << 20)

    with pytest.raises(HttpError, match="trop long"):
        UrllibHttp(opener=FakeOpener(Endless)).download(
            "https://example.test/m", tmp_path / "m.bin", max_bytes=2 << 20
        )
    assert Endless.reads <= 4


# --- redirections (M2) -------------------------------------------------------------------------


class _Scripted(urllib.request.BaseHandler):
    """Answers https:// with a redirect, then with a 200, without any socket."""

    handler_order = 100

    def __init__(self, location: str) -> None:
        self.location = location
        self.seen: list[urllib.request.Request] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.seen.append(req)
        headers = Message()
        if len(self.seen) == 1:
            headers["Location"] = self.location
            code = 302
        else:
            code = 200
        response = urllib.response.addinfourl(io.BytesIO(b"ok"), headers, req.full_url, code)
        response.msg = "x"  # type: ignore[attr-defined]
        return response


def scripted(location: str, *, follow: bool) -> tuple[Any, _Scripted]:
    handler = _Scripted(location)
    return build_opener(follow=follow, extra=[handler]), handler


def test_http_get_follows_https_redirects_and_drops_authorization_across_hosts() -> None:
    director, handler = scripted("https://cdn.other.test/file", follow=True)
    request = urllib.request.Request(
        "https://example.test/x", headers={"Authorization": "Bearer s"}
    )
    assert director.open(request, timeout=1).read() == b"ok"
    assert handler.seen[1].full_url == "https://cdn.other.test/file"
    assert handler.seen[1].get_header("Authorization") is None


def test_http_redirect_to_another_host_keeps_only_the_allowed_headers() -> None:
    director, handler = scripted("https://cdn.other.test/file", follow=True)
    request = urllib.request.Request(
        "https://example.test/x",
        headers={"X-Api-Key": "k3y", "Accept": "application/json", "User-Agent": "rushes/1"},
    )
    request.add_unredirected_header("X-Token", "t0k")
    director.open(request, timeout=1)
    second = handler.seen[1]
    sent = {name.lower() for name in (*second.headers, *second.unredirected_hdrs)}
    assert "x-api-key" not in sent and "x-token" not in sent
    assert second.get_header("Accept") == "application/json"
    assert second.get_header("User-agent") == "rushes/1"  # ours, not a secret


def test_http_redirect_on_the_same_host_keeps_authorization() -> None:
    director, handler = scripted("https://example.test/other", follow=True)
    request = urllib.request.Request(
        "https://example.test/x", headers={"Authorization": "Bearer s"}
    )
    director.open(request, timeout=1)
    assert handler.seen[1].get_header("Authorization") == "Bearer s"


@pytest.mark.parametrize("target", ["http://example.test/x", "ftp://example.test/x"])
def test_http_redirect_to_a_non_https_target_is_refused(target: str) -> None:
    director, handler = scripted(target, follow=True)
    with pytest.raises(HttpError, match="redirection refusée") as excinfo:
        director.open(urllib.request.Request("https://example.test/x"), timeout=1)
    assert len(handler.seen) == 1
    assert "non sécurisé" in (excinfo.value.hint or "")  # not the generic "check the network"
    assert "connexion réseau" not in (excinfo.value.hint or "")


def test_http_post_never_follows_a_redirect() -> None:
    director, handler = scripted("https://login.other.test/", follow=False)
    http = UrllibHttp(opener=director.open)
    with pytest.raises(HttpError) as excinfo:
        http.post("https://ntfy.test/t", body=b"b", headers={"Authorization": "Bearer s3cr3t"})
    assert excinfo.value.status == 302
    assert len(handler.seen) == 1  # the login page was never asked, nothing was replayed


def test_http_default_openers_follow_for_get_and_not_for_post() -> None:
    http = UrllibHttp()
    assert any(h.__class__.__name__ == "_NoRedirect" for h in http._post_opener.__self__.handlers)
    assert not any(h.__class__.__name__ == "_NoRedirect" for h in http._opener.__self__.handlers)


def test_http_download_refuses_a_declared_length_over_the_bound_without_reading(
    tmp_path: Path,
) -> None:
    class NoRead(FakeResponse):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("nothing should be read")

    opener = FakeOpener(lambda: NoRead(b"", length=1000))
    with pytest.raises(HttpError, match="trop long"):
        UrllibHttp(opener=opener).download("https://example.test/m", tmp_path / "m", max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_http_download_never_asks_for_more_than_one_byte_past_the_bound(tmp_path: Path) -> None:
    sizes: list[int] = []

    class Recording(FakeResponse):
        def read(self, size: int | None = -1) -> bytes:
            sizes.append(size or 0)
            return super().read(size)

    opener = FakeOpener(lambda: Recording(b"x" * 10))
    UrllibHttp(opener=opener).download("https://example.test/m", tmp_path / "m", max_bytes=10)
    assert sizes[0] == 11  # the extra byte tells "exactly 10" from "more than 10"
