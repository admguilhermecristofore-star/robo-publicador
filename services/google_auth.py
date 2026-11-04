"""Autenticação OAuth única para Sheets, Drive e YouTube.

Na 1ª execução abre o navegador para você autorizar; depois salva o token.json
e renova sozinho (necessário para o robô rodar de madrugada sem ninguém).
"""
import json
import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import config

log = logging.getLogger(__name__)

# Todos os escopos de uma vez -> um único consentimento para o projeto inteiro.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def obter_credenciais() -> Credentials:
    # Na nuvem: o token vem da variável GOOGLE_TOKEN_JSON (guardada no Secret Manager)
    token_nuvem = os.getenv("GOOGLE_TOKEN_JSON")
    if token_nuvem:
        creds = Credentials.from_authorized_user_info(json.loads(token_nuvem), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
        return creds

    creds = None
    token = config.GOOGLE_TOKEN_FILE

    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Renovando token do Google...")
        creds.refresh(Request())
    else:
        if not config.GOOGLE_CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                f"Arquivo {config.GOOGLE_CREDENTIALS_FILE.name} não encontrado. "
                "Baixe o OAuth Client (Desktop) no Google Cloud Console."
            )
        log.info("Abrindo navegador para autorizar a conta Google...")
        flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    token.write_text(creds.to_json(), encoding="utf-8")
    return creds
