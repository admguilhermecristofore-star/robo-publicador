"""Download dos vídeos do Google Drive para a pasta temp/."""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

import config
from services.google_auth import obter_credenciais

log = logging.getLogger(__name__)

_servico = None


def _drive():
    global _servico
    if _servico is None:
        _servico = build("drive", "v3", credentials=obter_credenciais(), cache_discovery=False)
    return _servico


def obter_info(file_id: str) -> dict:
    """Nome, tipo e tamanho do arquivo (também confirma que o robô tem acesso)."""
    return _drive().files().get(
        fileId=file_id, fields="id,name,mimeType,size", supportsAllDrives=True
    ).execute()


def baixar_video(file_id: str, destino: Path | None = None) -> Path:
    """Baixa o arquivo do Drive e devolve o caminho local."""
    destino = destino or config.TEMP_DIR
    info = obter_info(file_id)
    if not info.get("mimeType", "").startswith("video/"):
        raise ValueError(f"Arquivo do Drive não é vídeo: {info.get('name')} ({info.get('mimeType')})")

    nome_seguro = re.sub(r'[\\/:*?"<>|]+', "_", info["name"])
    caminho = destino / f"{file_id[:8]}_{nome_seguro}"
    tamanho_mb = int(info.get("size", 0)) / 1_048_576
    log.info("Baixando '%s' (%.1f MB)...", info["name"], tamanho_mb)

    request = _drive().files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(caminho, "wb") as arquivo:
        downloader = MediaIoBaseDownload(arquivo, request, chunksize=10 * 1024 * 1024)
        concluido = False
        while not concluido:
            _, concluido = downloader.next_chunk(num_retries=3)

    log.info("Download concluído: %s", caminho.name)
    return caminho


def apagar_temporario(caminho: Path | None) -> None:
    if caminho and caminho.exists():
        try:
            caminho.unlink()
        except OSError as e:
            log.warning("Não foi possível apagar %s: %s", caminho, e)
