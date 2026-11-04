"""Configurações centrais do robô (lidas do arquivo .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Pastas
TEMP_DIR = BASE_DIR / "temp"
LOGS_DIR = BASE_DIR / "logs"
TEMP_DIR.mkdir(exist_ok=True)

# Pasta com os vídeos prontos para subir (o nome do arquivo vai na coluna "Nome do Arquivo")
VIDEOS_DIR = Path(os.getenv("VIDEOS_DIR") or BASE_DIR.parent.parent / "videos")  # sistema -> bot_redes_sociais -> pasta principal
(VIDEOS_DIR / "postados").mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Google
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SHEET_NAME = os.getenv("SHEET_NAME", "Conteúdos")
TIMEZONE = os.getenv("TIMEZONE", "America/Sao_Paulo")
GOOGLE_CREDENTIALS_FILE = BASE_DIR / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = BASE_DIR / os.getenv("GOOGLE_TOKEN_FILE", "token.json")

# Meta
INSTAGRAM_ID_1 = os.getenv("INSTAGRAM_ID_1", "")
META_TOKEN_1 = os.getenv("META_TOKEN_1", "")
INSTAGRAM_ID_2 = os.getenv("INSTAGRAM_ID_2", "")
META_TOKEN_2 = os.getenv("META_TOKEN_2", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN", "")

# TikTok
TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN", "")

# Publicação
REDES_ATIVAS = os.getenv("REDES_ATIVAS", "youtube")            # ex.: youtube,instagram,facebook,tiktok
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "private")      # private | unlisted | public
MAX_POR_EXECUCAO = int(os.getenv("MAX_POR_EXECUCAO", "5"))     # trava de segurança por execução
YOUTUBE_ANTECEDENCIA_HORAS = float(os.getenv("YOUTUBE_ANTECEDENCIA_HORAS", "24"))  # 0 = não programa, publica na hora
META_API_VERSION = os.getenv("META_API_VERSION", "v23.0")
META_TOKEN = os.getenv("META_TOKEN", "")          # token do usuário do sistema (vale para os 2 perfis)
META_BUSINESS_ID = os.getenv("META_BUSINESS_ID", "")
TIKTOK_DIAS = int(os.getenv("TIKTOK_DIAS", "10"))  # quantos dias à frente o kit do TikTok mostra
ATRASO_MAX_HORAS = float(os.getenv("ATRASO_MAX_HORAS", "12"))  # não publica sozinho o que atrasou mais que isso
