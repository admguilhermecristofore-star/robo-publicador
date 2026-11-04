"""Publicação de Reels no Instagram — API oficial (Graph API com token de usuário do sistema).

Token: usuário do sistema "Publicador Reels" no portfólio Guilherme Cristofore (não expira).

Fluxo oficial para vídeo local (upload resumível, sem precisar de link público):
  1. cria o contêiner do Reel  (POST /{ig-id}/media, upload_type=resumable)
  2. envia o arquivo           (POST rupload.facebook.com/ig-api-upload/...)
  3. espera o processamento    (status_code = FINISHED)
  4. publica                   (POST /{ig-id}/media_publish)

O Instagram NÃO permite programar pela API: o robô publica no horário da Data Postar.
Limite da Meta: 50 publicações por conta a cada 24h (usamos bem menos).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

API = f"https://graph.facebook.com/{config.META_API_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{config.META_API_VERSION}"
LIMITE_LEGENDA = 2200
ARQUIVO_TOKENS = config.BASE_DIR / "meta_tokens.json"  # tokens renovados ficam aqui (fora do Git)


class ErroInstagram(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Contas e tokens
# ---------------------------------------------------------------------------
def contas_configuradas() -> dict[str, dict]:
    """{'instagram': {...perfil 1}, 'instagram2': {...perfil 2}} conforme o .env."""
    contas = {}
    for rede, n in (("instagram", "1"), ("instagram2", "2")):
        ig_id = getattr(config, f"INSTAGRAM_ID_{n}")
        token = getattr(config, f"META_TOKEN_{n}") or config.META_TOKEN
        if ig_id and token:
            contas[rede] = {"rede": rede, "ig_id": ig_id, "token_env": token}
    return contas


def _ler_tokens() -> dict:
    try:
        return json.loads(ARQUIVO_TOKENS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _salvar_token(ig_id: str, token: str) -> None:
    dados = _ler_tokens()
    dados[ig_id] = {"token": token, "renovado_em": datetime.now().isoformat(timespec="seconds")}
    ARQUIVO_TOKENS.write_text(json.dumps(dados, indent=2), encoding="utf-8")


def token_atual(conta: dict) -> str:
    """Token de usuário do sistema: não expira, não precisa renovar."""
    return conta["token_env"]


def _checar(resposta: requests.Response) -> dict:
    try:
        dados = resposta.json()
    except ValueError:
        raise ErroInstagram(f"HTTP {resposta.status_code}: {resposta.text[:300]}")
    if resposta.status_code >= 400 or "error" in dados:
        erro = dados.get("error", dados)
        raise ErroInstagram(f"{erro.get('message', erro)} (código {erro.get('code')}/{erro.get('error_subcode')})")
    return dados


def info_conta(conta: dict) -> dict:
    token = token_atual(conta)
    return _checar(requests.get(f"{API}/{conta['ig_id']}", params={
        "fields": "id,username,name,followers_count,media_count", "access_token": token}, timeout=30))


def descobrir_contas(token: str, business_id: str) -> list[dict]:
    """Lista os perfis do Instagram que o token enxerga (para preencher INSTAGRAM_ID_1/2)."""
    achados = {}
    tentativas = [
        (f"{API}/me/accounts", {"fields": "name,instagram_business_account{id,username}"}),
        (f"{API}/{business_id}/owned_instagram_accounts", {"fields": "id,username"}),
        (f"{API}/{business_id}/instagram_business_accounts", {"fields": "id,username"}),
        (f"{API}/{business_id}/instagram_accounts", {"fields": "id,username"}),
        (f"{API}/{business_id}/owned_pages", {"fields": "name,instagram_business_account{id,username}"}),
    ]
    try:
        eu = _checar(requests.get(f"{API}/me", params={"fields": "id,name", "access_token": token}, timeout=30))
        print(f"  token OK — usuário: {eu.get('name')} ({eu.get('id')})")
        perms = _checar(requests.get(f"{API}/me/permissions", params={"access_token": token}, timeout=30))
        print("  permissões:", ", ".join(p["permission"] for p in perms.get("data", []) if p.get("status") == "granted"))
    except (ErroInstagram, requests.RequestException) as e:
        print(f"  token com problema: {e}")
    for url, params in tentativas:
        rota = url.replace(API, "")
        try:
            dados = _checar(requests.get(url, params={**params, "access_token": token}, timeout=30))
            print(f"  {rota}: {len(dados.get('data', []))} item(ns)")
        except (ErroInstagram, requests.RequestException) as e:
            print(f"  {rota}: erro — {e}")
            continue
        for item in dados.get("data", []):
            ig = item.get("instagram_business_account") or item
            if ig.get("id") and ig.get("username"):
                achados[ig["id"]] = ig["username"]
    return [{"id": k, "username": v} for k, v in achados.items()]


# ---------------------------------------------------------------------------
# Publicação
# ---------------------------------------------------------------------------
def publicar_reel(video: Path, legenda: str, conta: dict, compartilhar_no_feed: bool = True,
                  espera_max_s: int = 900) -> str:
    """Publica o Reel e devolve o ID da publicação."""
    token = token_atual(conta)
    ig_id = conta["ig_id"]
    legenda = (legenda or "").strip()[:LIMITE_LEGENDA]

    # 1. contêiner
    dados = _checar(requests.post(f"{API}/{ig_id}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": legenda,
        "share_to_feed": "true" if compartilhar_no_feed else "false", "access_token": token,
    }, timeout=60))
    container = dados["id"]
    log.info("Instagram (%s): contêiner %s criado, enviando vídeo...", conta["rede"], container)

    # 2. upload do arquivo
    tamanho = video.stat().st_size
    with open(video, "rb") as f:
        r = requests.post(f"{RUPLOAD}/{container}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(tamanho)})
    resultado = _checar(r)
    if not resultado.get("success", True):
        raise ErroInstagram(f"Upload recusado: {resultado}")

    # 3. aguarda processamento
    inicio = time.time()
    while True:
        st = _checar(requests.get(f"{API}/{container}", params={
            "fields": "status_code,status", "access_token": token}, timeout=30))
        codigo = st.get("status_code")
        if codigo == "FINISHED":
            break
        if codigo in ("ERROR", "EXPIRED"):
            raise ErroInstagram(f"Instagram recusou o vídeo: {st.get('status')}")
        if time.time() - inicio > espera_max_s:
            raise ErroInstagram("Tempo esgotado esperando o Instagram processar o vídeo")
        time.sleep(10)

    # 4. publica
    pub = _checar(requests.post(f"{API}/{ig_id}/media_publish", data={
        "creation_id": container, "access_token": token}, timeout=60))
    media_id = pub["id"]
    try:
        link = _checar(requests.get(f"{API}/{media_id}", params={
            "fields": "permalink", "access_token": token}, timeout=30)).get("permalink")
    except ErroInstagram:
        link = None
    log.info("Instagram OK (%s): %s", conta["rede"], link or media_id)
    return media_id
