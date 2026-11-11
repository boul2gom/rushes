"""Real `Http` client on urllib (docs/plans/00-master.md section 5.8, task 9).

Never a cookie, never a secret in a message: errors name the host only (an URL path may hold a
ntfy topic), never headers or request bodies (R47).
"""

import http.client
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlencode, urlsplit

from rushes.net.base import HttpError

TIMEOUT_S = 30.0
CHUNK = 1 << 20
_RETRY_HINT = "Vérifie la connexion réseau puis relance la commande."


def _user_agent() -> str:
    try:
        return f"rushes/{package_version('rushes')}"
    except PackageNotFoundError:
        return "rushes/dev"


def _host(url: str) -> str:
    return urlsplit(url).hostname or "hôte inconnu"


def _status_error(url: str, status: int) -> HttpError:
    if status == 404:
        hint = "Adresse introuvable : l'URL a peut-être changé."
    elif status == 429:
        hint = "Trop de requêtes : réessaie plus tard."
    elif status in (401, 403):
        hint = "Accès refusé par le serveur."
    elif status >= 500:
        hint = "Erreur côté serveur : réessaie plus tard."
    else:
        hint = _RETRY_HINT
    return HttpError(
        f"requête refusée par {_host(url)} (statut {status})", status=status, hint=hint
    )


# the only request headers that follow a redirect to another host: ours, none of them secret
_ALLOWED_ACROSS_HOSTS = ("accept", "user-agent")


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects to https only; another host gets only the allowed headers."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        target = urlsplit(newurl)
        if target.scheme != "https":
            raise HttpError(
                f"redirection refusée par {_host(req.full_url)} (la cible n'est pas en https)",
                hint="L'URL redirige vers un hôte non sécurisé : signale-le, le manifeste est "
                "peut-être périmé.",
            )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and target.hostname != urlsplit(req.full_url).hostname:
            for store in (new.headers, new.unredirected_hdrs):
                for name in [n for n in store if n.lower() not in _ALLOWED_ACROSS_HOSTS]:
                    del store[name]
        return new


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx stays a 3xx (it becomes an `HttpError`): nothing is replayed elsewhere."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def build_opener(
    *, follow: bool, extra: Sequence[urllib.request.BaseHandler] = ()
) -> urllib.request.OpenerDirector:
    """An opener without cookie handler; `follow` chooses https-only redirects or none."""
    handler = _HttpsOnlyRedirect() if follow else _NoRedirect()
    return urllib.request.build_opener(handler, *extra)


def _declared_length(response: Any) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers else None
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


@contextmanager
def _network(url: str) -> Iterator[None]:
    """Turn what urllib raises into `HttpError` (status None for a network failure)."""
    try:
        yield
    except HttpError:
        raise
    except urllib.error.HTTPError as exc:
        raise _status_error(url, exc.code) from exc
    except TimeoutError as exc:
        raise HttpError(f"délai dépassé pour {_host(url)}", hint=_RETRY_HINT) from exc
    except http.client.HTTPException as exc:  # IncompleteRead, BadStatusLine...
        raise HttpError(f"réponse interrompue de {_host(url)}", hint=_RETRY_HINT) from exc
    except OSError as exc:  # URLError, connection reset, DNS failure...
        raise HttpError(f"échec de la connexion à {_host(url)}", hint=_RETRY_HINT) from exc


class UrllibHttp:
    """`Http` over urllib; `opener(request, timeout=...)` is injectable (no socket in tests)."""

    def __init__(
        self, *, timeout: float = TIMEOUT_S, opener: Callable[..., Any] | None = None
    ) -> None:
        self.timeout = float(timeout)
        # GET and download follow https redirects (Hugging Face, GitHub); POST follows none
        self._opener = opener or build_opener(follow=True).open
        self._post_opener = opener or build_opener(follow=False).open
        self._agent = _user_agent()

    def _open(
        self, url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None
    ) -> Any:
        opener = self._opener if data is None else self._post_opener
        if urlsplit(url).scheme not in ("http", "https"):  # never file:, ftp: ...
            raise HttpError(
                f"URL non prise en charge pour {_host(url)}", hint="Seul http(s) est permis."
            )
        request = urllib.request.Request(
            url,
            data=data,
            headers={"User-Agent": self._agent, **(headers or {})},
            method="POST" if data is not None else "GET",
        )
        with _network(url):
            response = opener(request, timeout=self.timeout)
        status = getattr(response, "status", 200)
        if not 200 <= status < 300:
            response.close()
            raise _status_error(url, status)
        return response

    def _body(self, url: str, response: Any) -> bytes:
        with response, _network(url):
            body: bytes = response.read()
        return body

    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any:
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        body = self._body(url, self._open(url, headers={"Accept": "application/json"}))
        try:
            return json.loads(body)
        except ValueError as exc:
            raise HttpError(f"réponse JSON invalide de {_host(url)}", hint=_RETRY_HINT) from exc

    def get_text(self, url: str) -> str:
        response = self._open(url)
        charset = response.headers.get_content_charset() if response.headers else None
        body = self._body(url, response)
        try:
            return body.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int:
        response = self._open(url, data=body, headers=headers)
        self._body(url, response)
        return int(response.status)

    def download(self, url: str, dest: Path, *, max_bytes: int | None = None) -> None:
        """Stream into `<dest>.part`, then rename: `dest` is either complete or untouched.

        More than `max_bytes`, or fewer bytes than a declared Content-Length, is an `HttpError`.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        too_long = HttpError(f"téléchargement trop long depuis {_host(url)}", hint=_RETRY_HINT)
        try:
            with self._open(url) as response, part.open("wb") as out:
                expected = _declared_length(response)
                if max_bytes is not None and expected is not None and expected > max_bytes:
                    raise too_long
                written = 0
                while True:
                    size = CHUNK if max_bytes is None else min(CHUNK, max_bytes - written + 1)
                    with _network(url):
                        chunk = response.read(size)
                    if not chunk:
                        break
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise too_long
                    out.write(chunk)  # a local write error stays an OSError
                if expected is not None and written != expected:
                    raise HttpError(
                        f"téléchargement incomplet depuis {_host(url)}", hint=_RETRY_HINT
                    )
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
