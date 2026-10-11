"""Integração com a YouTube Data API v3: login, upload, playlists e perfil do canal."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ROOT

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
TOKEN_FILE = ROOT / "token.json"
TOKEN_URI = "https://oauth2.googleapis.com/token"
RETRIABLE_STATUS = {500, 502, 503, 504}


class YouTubeError(RuntimeError):
    pass


# ---------------------------------------------------------------- credenciais
def authorize(client_secrets: str | Path) -> dict[str, str]:
    """Abre o navegador para autorizar o canal e salva o token (fazer uma vez, no seu PC)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return {
        "YOUTUBE_CLIENT_ID": creds.client_id,
        "YOUTUBE_CLIENT_SECRET": creds.client_secret,
        "YOUTUBE_REFRESH_TOKEN": creds.refresh_token,
    }


def load_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    refresh = os.environ.get("YOUTUBE_REFRESH_TOKEN")
    if refresh:
        creds = Credentials(
            token=None,
            refresh_token=refresh,
            token_uri=TOKEN_URI,
            client_id=os.environ.get("YOUTUBE_CLIENT_ID"),
            client_secret=os.environ.get("YOUTUBE_CLIENT_SECRET"),
            scopes=SCOPES,
        )
    elif TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_info(json.loads(TOKEN_FILE.read_text()), SCOPES)
    else:
        raise YouTubeError(
            "Credenciais do YouTube ausentes. Rode `python -m kidschannel auth --client-secrets client_secret.json` "
            "ou defina YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET e YOUTUBE_REFRESH_TOKEN."
        )
    try:
        creds.refresh(Request())
    except Exception as exc:
        raise YouTubeError(
            "Não foi possível renovar o acesso ao YouTube. Se o app OAuth estiver em modo 'Teste', "
            "o token expira em 7 dias: publique o app ('Em produção') e rode `auth` de novo."
        ) from exc
    return creds


# ---------------------------------------------------------------- metadados
def _clean(text: str) -> str:
    # O YouTube rejeita '<' e '>' em títulos e descrições.
    return text.replace("<", "").replace(">", "").strip()


def clean_title(title: str, limit: int = 100) -> str:
    title = " ".join(_clean(title).split())
    return title[:limit].rstrip() or "Vídeo infantil"


def clean_description(text: str, limit_bytes: int = 4900) -> str:
    text = _clean(text)
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    return encoded[:limit_bytes].decode("utf-8", errors="ignore").rstrip()


def clean_tags(tags: list[str], limit: int = 480) -> list[str]:
    """Limite do YouTube: 500 caracteres somando vírgulas (tags com espaço contam aspas)."""
    out, seen, used = [], set(), 0
    for tag in tags:
        tag = " ".join(_clean(tag).replace(",", " ").replace('"', "").split()).lstrip("#")
        key = tag.lower()
        if not tag or key in seen or len(tag) > 100:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)
        if used + cost > limit:
            break
        out.append(tag)
        seen.add(key)
        used += cost
    return out


def build_video_body(cfg: dict[str, Any], script, series: dict[str, Any], publish_at: datetime | None = None) -> dict[str, Any]:
    ch, pub = cfg["channel"], cfg["publishing"]
    hashtags = " ".join(["#shorts", *series.get("hashtags", [])])
    description = "\n\n".join(
        part for part in [script.description.strip(), ch.get("footer", "").strip(), hashtags] if part
    )
    status: dict[str, Any] = {
        "privacyStatus": pub.get("privacy_status", "public"),
        "selfDeclaredMadeForKids": bool(pub.get("made_for_kids", True)),
        "embeddable": True,
        "license": "youtube",
        "containsSyntheticMedia": bool(pub.get("contains_synthetic_media", False)),
    }
    if publish_at:
        status["privacyStatus"] = "private"  # exigido pelo YouTube para agendar
        status["publishAt"] = publish_at.isoformat()
    lang = ch.get("language", "pt-BR")
    return {
        "snippet": {
            "title": clean_title(script.title),
            "description": clean_description(description),
            "tags": clean_tags(list(script.tags) + list(ch.get("keywords", []))),
            "categoryId": str(pub.get("category_id", "27")),
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": status,
    }


# ---------------------------------------------------------------- cliente
class YouTubeClient:
    def __init__(self, credentials=None, http=None):
        from googleapiclient.discovery import build

        if http is not None:  # usado nos testes
            self.api = build("youtube", "v3", http=http, cache_discovery=False)
        else:
            self.api = build("youtube", "v3", credentials=credentials or load_credentials(), cache_discovery=False)

    def _execute(self, request):
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except HttpError as exc:
            raise YouTubeError(_explain(exc)) from exc

    def channel(self) -> dict[str, Any]:
        resp = self._execute(self.api.channels().list(part="snippet,brandingSettings,status", mine=True))
        if not resp.get("items"):
            raise YouTubeError("Esta conta Google ainda não tem um canal do YouTube. Crie em youtube.com/create_channel.")
        return resp["items"][0]

    def upload(self, video_path: Path, body: dict[str, Any]) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = self.api.videos().insert(part="snippet,status", body=body, media_body=media)
        response, retry = None, 0
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    log.info("Upload %d%%", int(status.progress() * 100))
            except HttpError as exc:
                if exc.resp.status not in RETRIABLE_STATUS:
                    raise YouTubeError(_explain(exc)) from exc
                retry = self._backoff(retry, exc)
            except (ConnectionError, TimeoutError, OSError) as exc:
                retry = self._backoff(retry, exc)
        if "id" not in response:
            raise YouTubeError(f"Upload terminou sem ID de vídeo: {response}")
        return response["id"]

    @staticmethod
    def _backoff(retry: int, exc: Exception) -> int:
        retry += 1
        if retry > 6:
            raise YouTubeError(f"Upload falhou após várias tentativas: {exc}")
        wait = random.uniform(0, 2**retry)
        log.warning("Erro temporário no upload (%s); nova tentativa em %.1fs", exc, wait)
        time.sleep(wait)
        return retry

    def create_playlist(self, title: str, description: str, language: str) -> str:
        body = {
            "snippet": {"title": clean_title(title, 150), "description": clean_description(description), "defaultLanguage": language},
            "status": {"privacyStatus": "public"},
        }
        return self._execute(self.api.playlists().insert(part="snippet,status", body=body))["id"]

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
        self._execute(self.api.playlistItems().insert(part="snippet", body=body))

    def upload_banner(self, image_path: Path) -> str:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(image_path), mimetype="image/jpeg", resumable=False)
        return self._execute(self.api.channelBanners().insert(media_body=media))["url"]

    def update_branding(self, cfg: dict[str, Any], banner_url: str | None = None) -> None:
        ch_cfg = cfg["channel"]
        current = self.channel()
        old = current.get("brandingSettings", {}).get("channel", {})
        # Envia só campos graváveis; os demais são ignorados ou apagados pela API.
        channel = {k: old[k] for k in ("unsubscribedTrailer", "trackingAnalyticsAccountId") if k in old}
        channel.update(
            {
                "description": clean_description(ch_cfg["description"], 1000),
                "keywords": " ".join(f'"{k}"' if " " in k else k for k in ch_cfg.get("keywords", []))[:500],
                "defaultLanguage": ch_cfg.get("language", "pt-BR"),
                "country": ch_cfg.get("country", "BR"),
            }
        )
        branding: dict[str, Any] = {"channel": channel}
        banner = banner_url or current.get("brandingSettings", {}).get("image", {}).get("bannerExternalUrl")
        if banner:
            branding["image"] = {"bannerExternalUrl": banner}
        self._execute(
            self.api.channels().update(part="brandingSettings", body={"id": current["id"], "brandingSettings": branding})
        )

    def mark_channel_for_kids(self) -> None:
        current = self.channel()
        body = {"id": current["id"], "status": {"selfDeclaredMadeForKids": True}}
        self._execute(self.api.channels().update(part="status", body=body))


def _explain(exc) -> str:
    try:
        payload = json.loads(exc.content.decode("utf-8"))
        error = payload.get("error", {})
        reason = (error.get("errors") or [{}])[0].get("reason", "")
        message = error.get("message", "")
    except Exception:
        reason, message = "", str(exc)
    hints = {
        "quotaExceeded": "Cota diária da API do YouTube esgotada (renova à meia-noite, horário do Pacífico).",
        "uploadLimitExceeded": "Limite de uploads do canal atingido por hoje.",
        "youtubeSignupRequired": "A conta Google não tem canal do YouTube; crie um em youtube.com/create_channel.",
        "forbidden": "Sem permissão. Confira se autorizou a conta dona do canal.",
    }
    return f"YouTube API ({exc.resp.status} {reason}): {hints.get(reason, message)}"
