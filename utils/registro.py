"""Registro do que já foi publicado — aba "Robô - Publicações" da própria planilha.

Evita postar o mesmo vídeo duas vezes na mesma rede (mesmo se uma execução cair no
meio, ou se o robô rodar no PC e na nuvem). Também serve de histórico para você.
A chave é o ID do arquivo no Drive (ou "arquivo:<nome>" para vídeo local).
"""
from __future__ import annotations

import logging
from datetime import datetime

import gspread

import config
from services.google_auth import obter_credenciais

log = logging.getLogger(__name__)

ABA = "Robô - Publicações"
CABECALHO = ["Data/hora", "Linha", "Vídeo (chave)", "Rede", "ID do post", "Link"]

_ws = None
_cache: dict[tuple[str, str], str] | None = None


def _aba():
    global _ws
    if _ws is None:
        planilha = gspread.authorize(obter_credenciais()).open_by_key(config.SPREADSHEET_ID)
        try:
            _ws = planilha.worksheet(ABA)
        except gspread.WorksheetNotFound:
            _ws = planilha.add_worksheet(ABA, rows=1000, cols=len(CABECALHO))
            _ws.append_row(CABECALHO, value_input_option="RAW")
            log.info("Aba '%s' criada na planilha", ABA)
    return _ws


def _carregar() -> dict[tuple[str, str], str]:
    global _cache
    if _cache is None:
        _cache = {}
        for linha in _aba().get_all_values()[1:]:
            if len(linha) >= 5 and linha[2] and linha[3]:
                _cache[(linha[2], linha[3])] = linha[4]
    return _cache


def ja_publicado(chave: str, rede: str) -> str | None:
    return _carregar().get((chave, rede))


def marcar(chave: str, rede: str, post_id: str, linha: int, link: str = "") -> None:
    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    _aba().append_row([agora, linha, chave, rede, str(post_id), link], value_input_option="RAW")
    _carregar()[(chave, rede)] = str(post_id)
