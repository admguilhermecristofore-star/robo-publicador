"""Leitura e escrita da aba "Conteúdos" da planilha "Copy de conteudo Guilherme".

As colunas são localizadas pelo NOME DO CABEÇALHO (linha 1), não pela letra.
Se alguém inserir ou mover uma coluna, o robô continua funcionando.

Layout atual da aba:
  A Editor | B Nome do Arquivo | C Transcrição | D Descrição / Copy
  E Título (YouTube - max 100 caracteres) | F Tag YouTube | G Data Postar
  H Status | I tipo | J Virais | K Mês | L Ano | M Guilherme 2 | N Link drive

Fluxo:
  - Você coloca o vídeo na pasta "videos" e o nome dele em Nome do Arquivo (B)
    (ou cola o Link drive em N), e cola a Transcrição (C).
  - As fórmulas =AI() da planilha geram Copy (D), Título (E) e Tags (F).
  - O robô publica as linhas 'Pendente' cuja Data Postar chegou e muda o Status.

O robô SÓ escreve na coluna Status. Copy/Título/Tags (fórmulas =AI) e as demais
colunas nunca são tocadas.

Teste rápido no terminal (na pasta do projeto):
  python -m services.sheets                     -> resumo da fila
  python -m services.sheets --linha 10          -> mostra uma linha
  python -m services.sheets --teste-escrita 10  -> escreve e desfaz (não altera nada no final)
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, fields
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

import gspread
from gspread.exceptions import APIError, WorksheetNotFound
from gspread.utils import rowcol_to_a1

import config
from services.google_auth import obter_credenciais

log = logging.getLogger(__name__)

TZ = ZoneInfo(config.TIMEZONE)
LIMITE_TITULO_YOUTUBE = 100


# ---------------------------------------------------------------------------
# Mapeamento: campo interno -> cabeçalho na planilha
# ---------------------------------------------------------------------------
COLUNAS = {
    "editor": "Editor",
    "nome_arquivo": "Nome do Arquivo",
    "transcricao": "Transcrição",
    "copy": "Descrição / Copy",
    "titulo": "Título",  # cabeçalho real: "Título (YouTube - max 100 caracteres)"
    "tags": "Tag YouTube",
    "data_postar": "Data Postar",
    "status": "Status",
    "tipo": "tipo",
    "virais": "Virais",
    "mes": "Mês",
    "ano": "Ano",
    "guilherme_2": "Guilherme 2",
    "link_drive": "Link drive",
}
OBRIGATORIAS = {"copy", "titulo", "tags", "data_postar", "status", "link_drive"}
EDITAVEIS = {"status"}  # protege as fórmulas =AI() e as colunas da equipe


class Status:
    """Valores da coluna Status (minúsculo, igual ao padrão já usado na planilha)."""
    PENDENTE = "pendente"        # pronto para publicar na Data Postar
    AGENDADO = "agendado"        # sinônimo de pendente (também é publicado)
    PROCESSANDO = "processando"  # robô publicando esta linha (trava)
    POSTADO = "postado"
    ERRO = "erro"


FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------
def _normalizar(texto: str) -> str:
    """'Título (YouTube)' -> 'titulo (youtube)'  (sem acento, minúsculo, espaços únicos)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def parse_data(valor: str) -> datetime | None:
    valor = (valor or "").strip()
    for fmt in FORMATOS_DATA:
        try:
            return datetime.strptime(valor, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def extrair_id_drive(link: str) -> str | None:
    """Aceita link /file/d/ID/..., ?id=ID ou o ID puro."""
    link = (link or "").strip()
    if not link:
        return None
    for padrao in (r"/d/([\w-]{20,})", r"[?&]id=([\w-]{20,})"):
        m = re.search(padrao, link)
        if m:
            return m.group(1)
    return link if re.fullmatch(r"[\w-]{20,}", link) else None


def _com_retry(tentativas: int = 5):
    """Repete a chamada em erros temporários da API (cota 429 / 5xx) com espera crescente."""
    def decorador(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for n in range(1, tentativas + 1):
                try:
                    return func(*args, **kwargs)
                except APIError as e:
                    codigo = getattr(getattr(e, "response", None), "status_code", 0)
                    if codigo not in (429, 500, 502, 503, 504) or n == tentativas:
                        raise
                    espera = 2 ** n
                    log.warning("Sheets API %s em %s — nova tentativa em %ss (%s/%s)",
                                codigo, func.__name__, espera, n, tentativas)
                    time.sleep(espera)
        return wrapper
    return decorador


# ---------------------------------------------------------------------------
# Modelo de uma linha
# ---------------------------------------------------------------------------
@dataclass
class Conteudo:
    linha: int  # número da linha na planilha (1 = cabeçalho)
    editor: str = ""
    nome_arquivo: str = ""
    transcricao: str = ""
    copy: str = ""
    titulo: str = ""
    tags: str = ""
    data_postar: str = ""
    status: str = ""
    tipo: str = ""
    virais: str = ""
    mes: str = ""
    ano: str = ""
    guilherme_2: str = ""
    link_drive: str = ""

    @property
    def status_norm(self) -> str:
        return _normalizar(self.status)

    @property
    def data_postar_dt(self) -> datetime | None:
        return parse_data(self.data_postar)

    @property
    def drive_file_id(self) -> str | None:
        return extrair_id_drive(self.link_drive)

    @property
    def chave(self) -> str:
        """Identificador único do vídeo (usado no registro anti-duplicação)."""
        return self.drive_file_id or f"arquivo:{self.nome_arquivo.strip().lower()}"

    @property
    def problemas(self) -> list[str]:
        """O que impede a publicação desta linha (lista vazia = pode publicar)."""
        faltando = []
        if not self.drive_file_id:
            from services.videos import encontrar_local
            if not self.nome_arquivo:
                faltando.append("Nome do Arquivo (ou Link drive)")
            elif not encontrar_local(self.nome_arquivo):
                faltando.append(f"vídeo '{self.nome_arquivo}' na pasta videos")
        for campo, nome in (("copy", "Copy"), ("titulo", "Título"), ("tags", "Tags")):
            valor = getattr(self, campo).strip()
            # =AI() ainda não gerado ou com erro aparece vazio, "#ERROR!", "#N/A", "Carregando..."
            if not valor or valor.startswith("#") and len(valor) < 15 or valor.lower().startswith(("carregando", "loading", "=ai(")):
                faltando.append(nome)
        if self.data_postar_dt is None:
            faltando.append("Data Postar")
        return faltando

    def __str__(self) -> str:
        return (f"Linha {self.linha} | {self.status or '-'} | {self.data_postar or 'sem data'} | "
                f"{(self.titulo or self.nome_arquivo or self.copy[:50] or '(vazio)')!r}")


# ---------------------------------------------------------------------------
# Serviço da planilha
# ---------------------------------------------------------------------------
class PlanilhaConteudos:
    def __init__(self, spreadsheet_id: str | None = None, aba: str | None = None):
        self.spreadsheet_id = spreadsheet_id or config.SPREADSHEET_ID
        self.aba = aba or config.SHEET_NAME
        if not self.spreadsheet_id:
            raise ValueError("SPREADSHEET_ID não definido no .env")
        self._ws: gspread.Worksheet | None = None
        self._colunas: dict[str, int] = {}  # campo -> índice (1-based)

    # ---------- conexão ----------
    @property
    def ws(self) -> gspread.Worksheet:
        if self._ws is None:
            cliente = gspread.authorize(obter_credenciais())
            planilha = cliente.open_by_key(self.spreadsheet_id)
            try:
                self._ws = planilha.worksheet(self.aba)
            except WorksheetNotFound:
                abas = [w.title for w in planilha.worksheets()]
                raise ValueError(f"Aba '{self.aba}' não encontrada. Abas disponíveis: {abas}")
            log.info("Conectado à planilha '%s' / aba '%s'", planilha.title, self.aba)
        return self._ws

    def _mapear_colunas(self, cabecalho: list[str]) -> None:
        normalizados = [_normalizar(c) for c in cabecalho]
        mapa: dict[str, int] = {}
        for campo, nome in COLUNAS.items():
            alvo = _normalizar(nome)
            # 1º tenta nome exato, depois "começa com" (ex.: "Título (YouTube - max...)")
            idx = next((i for i, h in enumerate(normalizados) if h == alvo), None)
            if idx is None:
                idx = next((i for i, h in enumerate(normalizados) if h.startswith(alvo)), None)
            if idx is not None:
                mapa[campo] = idx + 1
        faltando = OBRIGATORIAS - mapa.keys()
        if faltando:
            nomes = [COLUNAS[c] for c in sorted(faltando)]
            raise ValueError(f"Colunas obrigatórias não encontradas no cabeçalho: {nomes}")
        self._colunas = mapa

    def _linha_para_conteudo(self, numero: int, valores: list[str]) -> Conteudo:
        dados = {}
        for campo, col in self._colunas.items():
            dados[campo] = valores[col - 1].strip() if col - 1 < len(valores) else ""
        return Conteudo(linha=numero, **dados)

    # ---------- leitura ----------
    @_com_retry()
    def listar(self, *status: str) -> list[Conteudo]:
        """Todas as linhas preenchidas. Filtra por status se informado (sem diferenciar maiúsc./acento)."""
        valores = self.ws.get_all_values()  # 1 única chamada de API
        if not valores:
            return []
        self._mapear_colunas(valores[0])
        filtro = {_normalizar(s) for s in status}

        conteudos = []
        for numero, linha in enumerate(valores[1:], start=2):
            if not any(c.strip() for c in linha):
                continue
            item = self._linha_para_conteudo(numero, linha)
            if filtro and item.status_norm not in filtro:
                continue
            conteudos.append(item)
        return conteudos

    def buscar_para_publicar(self, agora: datetime | None = None) -> list[Conteudo]:
        """Status 'pendente'/'agendado' cuja Data Postar já chegou e com tudo preenchido.

        Ordenados pela data (mais antigo primeiro).
        """
        agora = agora or datetime.now(TZ)
        prontos = []
        for c in self.listar(Status.PENDENTE, Status.AGENDADO):
            data = c.data_postar_dt
            if data is not None and data > agora:
                continue
            if data is not None and config.ATRASO_MAX_HORAS > 0 and \
                    (agora - data).total_seconds() > config.ATRASO_MAX_HORAS * 3600:
                log.warning("Linha %s ignorada — Data Postar (%s) passou há mais de %sh; publique manualmente "
                            "(PUBLICAR LINHA AGORA) ou mude a data", c.linha, c.data_postar, config.ATRASO_MAX_HORAS)
                continue
            if c.problemas:
                log.warning("Linha %s ignorada — falta: %s", c.linha, ", ".join(c.problemas))
                continue
            prontos.append(c)
        return sorted(prontos, key=lambda c: c.data_postar_dt)

    def buscar_proximos(self, horas: float, agora: datetime | None = None) -> list[Conteudo]:
        """Pendentes completos cuja Data Postar está nas próximas X horas (para programar no YouTube)."""
        from datetime import timedelta
        agora = agora or datetime.now(TZ)
        limite = agora + timedelta(hours=horas)
        itens = [c for c in self.listar(Status.PENDENTE, Status.AGENDADO)
                 if not c.problemas and agora < c.data_postar_dt <= limite]
        return sorted(itens, key=lambda c: c.data_postar_dt)

    @_com_retry()
    def obter(self, linha: int) -> Conteudo:
        if not self._colunas:
            self._mapear_colunas(self.ws.row_values(1))
        return self._linha_para_conteudo(linha, self.ws.row_values(linha))

    # ---------- escrita ----------
    @_com_retry()
    def _confirmar_linha(self, conteudo: Conteudo) -> int:
        """Garante que a linha ainda é a mesma (alguém pode ter ordenado/inserido linhas).

        Usa o Link drive como identificador; se mudou de lugar, procura a nova posição.
        """
        chave_campo = "link_drive" if conteudo.link_drive else "nome_arquivo"
        chave = getattr(conteudo, chave_campo)
        if not chave or chave_campo not in self._colunas:
            return conteudo.linha  # sem identificador confiável: usa a linha original

        col = self._colunas[chave_campo]
        if self.ws.cell(conteudo.linha, col).value == chave:
            return conteudo.linha

        coluna_inteira = self.ws.col_values(col)
        for i, valor in enumerate(coluna_inteira, start=1):
            if i > 1 and valor == chave:
                log.warning("Linha mudou de posição: %s -> %s", conteudo.linha, i)
                conteudo.linha = i
                return i
        raise LookupError(f"Conteúdo da linha {conteudo.linha} não encontrado na planilha ({chave_campo}={chave!r})")

    @_com_retry()
    def atualizar(self, conteudo: Conteudo | int, **campos: str) -> None:
        """Atualiza um ou mais campos de uma linha em UMA chamada de API.

        Ex.: planilha.atualizar(item, copy="...", titulo="...", tags="a, b, c")
        """
        invalidos = set(campos) - EDITAVEIS
        if invalidos:
            raise ValueError(f"Campos não editáveis pelo robô: {invalidos}. Permitidos: {EDITAVEIS}")
        if not self._colunas:
            self._mapear_colunas(self.ws.row_values(1))

        linha = self._confirmar_linha(conteudo) if isinstance(conteudo, Conteudo) else conteudo

        if "titulo" in campos and campos["titulo"]:
            campos["titulo"] = campos["titulo"].strip()[:LIMITE_TITULO_YOUTUBE]
        if "tags" in campos and isinstance(campos["tags"], (list, tuple)):
            campos["tags"] = ", ".join(t.strip() for t in campos["tags"] if t.strip())

        lote = [
            {"range": rowcol_to_a1(linha, self._colunas[campo]), "values": [[valor]]}
            for campo, valor in campos.items()
            if campo in self._colunas
        ]
        if lote:
            self.ws.batch_update(lote, value_input_option="USER_ENTERED")
            if isinstance(conteudo, Conteudo):
                for campo, valor in campos.items():
                    setattr(conteudo, campo, valor)
            log.info("Linha %s atualizada: %s", linha, ", ".join(campos))

    def atualizar_status(self, conteudo: Conteudo | int, status: str, mensagem: str | None = None) -> None:
        """Muda o Status. Em caso de erro, grava a mensagem como NOTA na célula (passe o mouse para ver)."""
        self.atualizar(conteudo, status=status)
        linha = conteudo.linha if isinstance(conteudo, Conteudo) else conteudo
        celula = rowcol_to_a1(linha, self._colunas["status"])
        try:
            if mensagem:
                carimbo = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
                self.ws.insert_note(celula, f"[{carimbo}] {mensagem}"[:2000])
            elif status != Status.ERRO:
                self.ws.clear_note(celula)
        except APIError as e:  # nota é opcional; não derruba o fluxo
            log.warning("Não foi possível gravar nota em %s: %s", celula, e)


# ---------------------------------------------------------------------------
# Teste pelo terminal
# ---------------------------------------------------------------------------
def _cli() -> None:
    import argparse
    from collections import Counter

    from utils.logger import configurar_logs

    configurar_logs()
    p = argparse.ArgumentParser(description="Teste do módulo Google Sheets")
    p.add_argument("--aba", help="Usar outra aba (ex.: uma cópia de teste)")
    p.add_argument("--linha", type=int, help="Mostrar todos os campos de uma linha")
    p.add_argument("--teste-escrita", type=int, metavar="LINHA",
                   help="Escreve 'processando' no Status da linha e restaura o valor original")
    args = p.parse_args()

    planilha = PlanilhaConteudos(aba=args.aba)

    if args.linha:
        item = planilha.obter(args.linha)
        for f in fields(item):
            valor = str(getattr(item, f.name))
            print(f"{f.name:>13}: {valor[:120]}{'...' if len(valor) > 120 else ''}")
        print(f"{'drive_file_id':>13}: {item.drive_file_id}")
        return

    if args.teste_escrita:
        item = planilha.obter(args.teste_escrita)
        original = item.status
        print(f"Status original: {original!r}")
        planilha.atualizar(item, status=Status.PROCESSANDO)
        print("Escrito 'processando' — confira na planilha. Restaurando em 5s...")
        time.sleep(5)
        planilha.atualizar(item, status=original)
        print(f"Restaurado para {original!r}. Escrita OK.")
        return

    todos = planilha.listar()
    print(f"\nTotal de linhas preenchidas: {len(todos)}")
    print("Por status:")
    for status, qtd in Counter(c.status_norm or "(vazio)" for c in todos).most_common():
        print(f"  {status:<15} {qtd}")

    pendentes = planilha.listar(Status.PENDENTE, Status.AGENDADO)
    print(f"\nPendentes: {len(pendentes)}")
    for c in pendentes[:30]:
        situacao = "OK" if not c.problemas else "falta " + ", ".join(c.problemas)
        print(f"   linha {c.linha:<5} {c.data_postar:<20} {situacao}")
    print("\nProntos para publicar AGORA (data já chegou):")
    for c in planilha.buscar_para_publicar()[:10]:
        print("  ", c)


if __name__ == "__main__":
    _cli()
