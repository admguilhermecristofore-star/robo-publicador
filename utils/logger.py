"""Log no console + arquivo rotativo em logs/robo.log."""
import logging
from logging.handlers import RotatingFileHandler

import config

_FORMATO = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configurar_logs(nivel: int = logging.INFO) -> None:
    raiz = logging.getLogger()
    if raiz.handlers:  # evita duplicar handlers
        return
    raiz.setLevel(nivel)
    fmt = logging.Formatter(_FORMATO, "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    raiz.addHandler(console)

    arquivo = RotatingFileHandler(
        config.LOGS_DIR / "robo.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8"
    )
    arquivo.setFormatter(fmt)
    raiz.addHandler(arquivo)

    # bibliotecas do Google são verbosas
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
