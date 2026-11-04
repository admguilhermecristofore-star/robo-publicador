"""Publicador TikTok — Content Posting API oficial (Direct Post, envio do arquivo).

Fluxo:
  1) python main.py conectar-tiktok  -> abre a autorização no navegador, você copia o código
     da página de retorno e o robô troca pelo token (fica em tiktok_token.json, renova sozinho).
  2) publicar_video(caminho, legenda) -> creator_info -> init -> upload em partes -> acompanha status.

Enquanto o app não for auditado pelo TikTok, o post só pode sair como privado (SELF_ONLY).
"""
from __future__ import annotations

import json
import logging
import math
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

import config

log = logging.getLogger("robo.tiktok")

API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
ESCOPOS = "user.info.basic,video.publish,video.upload"
TOKEN_FILE = config.BASE_DIR / "tiktok_token.json"

MB = 1024 * 1024
CHUNK = 10 * MB            # partes de 10 MB (regra da API: 5–64 MB; a última pode ir até 128 MB)
TIPOS = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


class ErroTikTok(RuntimeError):
    pass


# ---------------------------------------------------------------- autorização
def _cfg(nome: str) -> str:
    v = os.getenv(nome) or getattr(config, nome, "")
    if not v:
        raise ErroTikTok(f"{nome} vazio no .env")
    return v


def redirect_uri() -> str:
    return os.getenv("TIKTOK_REDIRECT_URI", "")


def link_autorizacao(state: str | None = None) -> str:
    q = {
        "client_key": _cfg("TIKTOK_CLIENT_KEY"),
        "scope": ESCOPOS,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "state": state or secrets.token_urlsafe(12),
    }
    return f"{AUTH_URL}?{urlencode(q)}"


def _salvar(tok: dict) -> dict:
    agora = time.time()
    tok["expira_em"] = agora + int(tok.get("expires_in", 86400)) - 300
    tok["refresh_expira_em"] = agora + int(tok.get("refresh_expires_in", 31536000))
    if not os.getenv("TIKTOK_TOKEN_JSON"):
        TOKEN_FILE.write_text(json.dumps(tok, indent=2), encoding="utf-8")
    return tok


def _token_req(dados: dict) -> dict:
    dados = {"client_key": _cfg("TIKTOK_CLIENT_KEY"), "client_secret": _cfg("TIKTOK_CLIENT_SECRET"), **dados}
    r = requests.post(f"{API}/oauth/token/", data=dados, timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    j = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in j:
        raise ErroTikTok(f"token: {j.get('error')} — {j.get('error_description') or r.text[:300]}")
    return j


def trocar_codigo(codigo: str) -> dict:
    return _salvar(_token_req({"code": codigo.strip(), "grant_type": "authorization_code",
                               "redirect_uri": redirect_uri()}))


def _carregar() -> dict | None:
    bruto = os.getenv("TIKTOK_TOKEN_JSON")
    if bruto:
        return json.loads(bruto)
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return None


def token_valido() -> str:
    tok = _carregar()
    if not tok:
        raise ErroTikTok("TikTok não conectado — rode CONECTAR TIKTOK.bat")
    if time.time() >= tok.get("expira_em", 0):
        if time.time() >= tok.get("refresh_expira_em", 0):
            raise ErroTikTok("Conexão do TikTok expirou — rode CONECTAR TIKTOK.bat de novo")
        log.info("Renovando token do TikTok")
        novo = _token_req({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        tok = _salvar(novo)
    return tok["access_token"]


# ---------------------------------------------------------------- chamadas
def _post(caminho: str, corpo: dict | None = None) -> dict:
    r = requests.post(f"{API}{caminho}", json=corpo or {}, timeout=60, headers={
        "Authorization": f"Bearer {token_valido()}",
        "Content-Type": "application/json; charset=UTF-8",
    })
    j = r.json() if r.content else {}
    erro = (j.get("error") or {})
    if r.status_code != 200 or erro.get("code", "ok") != "ok":
        raise ErroTikTok(f"{caminho}: {erro.get('code')} — {erro.get('message') or r.text[:300]}")
    return j.get("data") or {}


def info_criador() -> dict:
    """Perfil autorizado + opções de privacidade que a conta aceita agora."""
    return _post("/post/publish/creator_info/query/")


def _partes(tamanho: int) -> tuple[int, int]:
    if tamanho <= 64 * MB:
        return tamanho, 1
    return CHUNK, tamanho // CHUNK  # a sobra vai junto na última parte


def publicar_video(video: Path, legenda: str, privacidade: str | None = None,
                   esperar_seg: int = 600) -> str:
    video = Path(video)
    criador = info_criador()
    opcoes = criador.get("privacy_level_options") or []
    priv = (privacidade or os.getenv("TIKTOK_PRIVACY") or "SELF_ONLY").upper()
    if opcoes and priv not in opcoes:
        log.warning("Privacidade %s não liberada para esta conta (%s); usando SELF_ONLY", priv, opcoes)
        priv = "SELF_ONLY"
    log.info("TikTok @%s — privacidade %s", criador.get("creator_username"), priv)

    tamanho = video.stat().st_size
    chunk, total = _partes(tamanho)
    init = _post("/post/publish/video/init/", {
        "post_info": {
            "title": (legenda or "")[:2200],
            "privacy_level": priv,
            "disable_duet": bool(criador.get("duet_disabled")),
            "disable_stitch": bool(criador.get("stitch_disabled")),
            "disable_comment": bool(criador.get("comment_disabled")),
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho,
                        "chunk_size": chunk, "total_chunk_count": total},
    })
    publish_id, upload_url = init["publish_id"], init["upload_url"]

    tipo = TIPOS.get(video.suffix.lower(), "video/mp4")
    with open(video, "rb") as f:
        for i in range(total):
            inicio = i * chunk
            fim = tamanho - 1 if i == total - 1 else inicio + chunk - 1
            f.seek(inicio)
            dados = f.read(fim - inicio + 1)
            for tentativa in range(4):
                r = requests.put(upload_url, data=dados, timeout=300, headers={
                    "Content-Type": tipo,
                    "Content-Length": str(len(dados)),
                    "Content-Range": f"bytes {inicio}-{fim}/{tamanho}",
                })
                if r.status_code in (200, 201, 206):
                    break
                log.warning("Parte %s/%s: HTTP %s — tentando de novo", i + 1, total, r.status_code)
                time.sleep(2 ** tentativa * 3)
            else:
                raise ErroTikTok(f"upload falhou na parte {i + 1}/{total}: HTTP {r.status_code} {r.text[:200]}")
            log.info("TikTok: parte %s/%s enviada", i + 1, total)

    limite = time.time() + esperar_seg
    while time.time() < limite:
        st = _post("/post/publish/status/fetch/", {"publish_id": publish_id})
        status = st.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
            log.info("TikTok publicado (%s)", ids[0] if ids else publish_id)
            return str(ids[0]) if ids else publish_id
        if status == "FAILED":
            raise ErroTikTok(f"TikTok recusou o vídeo: {st.get('fail_reason')}")
        time.sleep(8)
    log.warning("TikTok ainda processando após %ss — fica registrado pelo publish_id", esperar_seg)
    return publish_id
