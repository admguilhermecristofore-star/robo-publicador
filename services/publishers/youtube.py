"""Publicação no YouTube Shorts (YouTube Data API v3 — API oficial).

Cota: cada upload gasta 1.600 de 10.000 unidades/dia -> no máximo 6 vídeos por dia.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config
from services.google_auth import obter_credenciais

log = logging.getLogger(__name__)

CATEGORIA_EDUCACAO = "27"
_servico = None


def _yt():
    global _servico
    if _servico is None:
        _servico = build("youtube", "v3", credentials=obter_credenciais(), cache_discovery=False)
    return _servico


def canal_conectado() -> dict:
    """Mostra em qual canal o robô vai publicar."""
    r = _yt().channels().list(part="snippet,statistics", mine=True).execute()
    if not r.get("items"):
        raise RuntimeError("Nenhum canal do YouTube nesta conta Google")
    c = r["items"][0]
    return {"id": c["id"], "nome": c["snippet"]["title"],
            "inscritos": c["statistics"].get("subscriberCount", "?")}


def _limpar(texto: str) -> str:
    return re.sub(r"[<>]", "", texto or "").strip()  # YouTube rejeita < e >


def _preparar_tags(tags: str) -> list[str]:
    saida, total = [], 0
    for t in (tags or "").split(","):
        t = _limpar(t).strip("#").strip()
        if not t or t.lower() in (x.lower() for x in saida):
            continue
        custo = len(t) + (2 if " " in t else 0) + 1  # YouTube conta aspas em tags com espaço
        if total + custo > 490:
            break
        saida.append(t)
        total += custo
    return saida


def publicar_short(video: Path, titulo: str, descricao: str, tags: str,
                   privacidade: str | None = None, publicar_em: datetime | None = None) -> str:
    """Faz o upload e devolve o ID do vídeo.

    publicar_em: se a privacidade for "public" e a data for futura (mais de 15 min), o vídeo
    sobe PROGRAMADO e o YouTube o torna público sozinho nessa data/hora (igual ao Studio).
    Com YOUTUBE_PRIVACY=private nada vai ao ar: o vídeo fica privado, sem data.
    """
    privacidade = privacidade or config.YOUTUBE_PRIVACY
    agendar = (privacidade == "public" and publicar_em is not None
               and publicar_em > datetime.now(timezone.utc) + timedelta(minutes=15))
    corpo = {
        "snippet": {
            "title": _limpar(titulo)[:100],
            "description": _limpar(descricao)[:5000],
            "tags": _preparar_tags(tags),
            "categoryId": CATEGORIA_EDUCACAO,
            "defaultLanguage": "pt-BR",
            "defaultAudioLanguage": "pt-BR",
        },
        "status": {
            "privacyStatus": privacidade,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }
    if agendar:
        corpo["status"]["privacyStatus"] = "private"
        corpo["status"]["publishAt"] = publicar_em.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    pedido = _yt().videos().insert(part="snippet,status", body=corpo, media_body=media,
                                   notifySubscribers=True)

    quando = f"programado p/ {publicar_em:%d/%m/%Y %H:%M}" if agendar else privacidade
    log.info("YouTube: enviando '%s' (%s)...", corpo["snippet"]["title"], quando)
    resposta, tentativas = None, 0
    while resposta is None:
        try:
            _, resposta = pedido.next_chunk()
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and tentativas < 5:
                tentativas += 1
                time.sleep(2 ** tentativas)
                continue
            if e.resp.status == 403 and b"quotaExceeded" in e.content:
                raise RuntimeError("Cota diária do YouTube esgotada (máx. ~6 uploads/dia)") from e
            raise
    video_id = resposta["id"]
    log.info("YouTube OK: https://youtube.com/shorts/%s", video_id)
    return video_id
