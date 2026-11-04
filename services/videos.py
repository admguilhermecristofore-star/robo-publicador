"""Localiza o vídeo de cada linha.

Prioridade:
  1. Pasta local (VIDEOS_DIR): arquivo com o nome da coluna "Nome do Arquivo" (com ou sem extensão)
  2. Google Drive: link da coluna "Link drive" (baixado para temp/)

Depois de publicado, o vídeo local é movido para VIDEOS_DIR/postados.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import config

log = logging.getLogger(__name__)

EXTENSOES = {".mp4", ".mov", ".m4v", ".webm"}
PASTA_POSTADOS = "postados"


def _normalizar(nome: str) -> str:
    return nome.strip().lower()


def encontrar_local(nome_arquivo: str) -> Path | None:
    nome = _normalizar(nome_arquivo or "")
    if not nome or not config.VIDEOS_DIR.exists():
        return None
    for arquivo in config.VIDEOS_DIR.rglob("*"):
        if PASTA_POSTADOS in arquivo.relative_to(config.VIDEOS_DIR).parts[:1]:
            continue
        if arquivo.is_file() and arquivo.suffix.lower() in EXTENSOES:
            if _normalizar(arquivo.name) == nome or _normalizar(arquivo.stem) == nome:
                return arquivo
    return None


def obter_video(item) -> tuple[Path, bool]:
    """Devolve (caminho, é_temporario). Temporário = baixado do Drive, deve ser apagado."""
    local = encontrar_local(item.nome_arquivo)
    if local:
        log.info("Vídeo local: %s", local.name)
        return local, False
    if item.drive_file_id:
        from services import drive
        return drive.baixar_video(item.drive_file_id), True
    raise FileNotFoundError(
        f"Vídeo '{item.nome_arquivo}' não encontrado em {config.VIDEOS_DIR} e sem Link drive"
    )


def finalizar(caminho: Path | None, temporario: bool) -> None:
    """Apaga o temporário do Drive ou move o local para a pasta postados."""
    if not caminho or not caminho.exists():
        return
    try:
        if temporario:
            caminho.unlink()
        else:
            destino = config.VIDEOS_DIR / PASTA_POSTADOS / caminho.name
            shutil.move(str(caminho), str(destino))
            log.info("Movido para postados/: %s", caminho.name)
    except OSError as e:
        log.warning("Não foi possível limpar %s: %s", caminho, e)
