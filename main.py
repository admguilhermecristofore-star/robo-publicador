"""Orquestrador do robô.

Comandos (na pasta do projeto, com o venv ativo):
  python -m services.sheets                     -> mostra a fila e o que falta em cada linha
  python main.py canal                          -> mostra o canal do YouTube conectado
  python main.py contas                         -> mostra os perfis do Instagram conectados
  python main.py publicar                       -> rodada normal (é isso que o agendador do Windows executa):
                                                   1) YouTube: sobe PROGRAMADO o que vai ao ar nas próximas 24h
                                                   2) publica nas redes o que já chegou na Data Postar
  python main.py publicar --linha 936           -> processa só essa linha (programa se a data for futura)
  python main.py publicar --linha 936 --teste   -> envia como PRIVADO, sem data, sem mexer na planilha
"""
import argparse
import logging
import sys

import config
from services import videos
from services.publishers import instagram, tiktok, youtube
from services.sheets import Conteudo, PlanilhaConteudos, Status
from utils import registro
from utils.logger import configurar_logs

log = logging.getLogger("robo")


def _youtube(video, item, teste):
    if teste:
        return youtube.publicar_short(video, item.titulo, item.copy, item.tags, privacidade="private")
    return youtube.publicar_short(video, item.titulo, item.copy, item.tags, publicar_em=item.data_postar_dt)


def _instagram(rede):
    def publicar(video, item, teste):
        conta = instagram.contas_configuradas().get(rede)
        if not conta:
            raise RuntimeError(f"{rede}: INSTAGRAM_ID/META_TOKEN não preenchidos no .env")
        if teste:
            log.warning("Instagram não tem modo privado: o --teste PUBLICA de verdade no perfil")
        return instagram.publicar_reel(video, item.copy, conta)
    return publicar


def _tiktok(video, item, teste):
    # sem auditoria o TikTok só aceita privado; no --teste força privado sempre
    return tiktok.publicar_video(video, item.copy, privacidade="SELF_ONLY" if teste else None)


# rede -> função (caminho_video, item, teste) -> ID do post
PUBLICADORES = {
    "youtube": _youtube,
    "instagram": _instagram("instagram"),
    "instagram2": _instagram("instagram2"),
    "tiktok": _tiktok,
}
# redes que aceitam programar com antecedência (as outras publicam na hora)
PROGRAMAVEIS = {"youtube"}


def redes_do_item(item: Conteudo, redes: list[str]) -> list[str]:
    """instagram2 (@cortes) só entra se a coluna M "Guilherme 2" estiver preenchida."""
    return [r for r in redes if r != "instagram2" or item.guilherme_2.strip()]


def publicar_linha(planilha: PlanilhaConteudos, item: Conteudo, redes: list[str], teste: bool,
                   antecipado: bool = False) -> bool:
    """Publica a linha nas redes indicadas.

    antecipado=True: só programa nas redes PROGRAMAVEIS e mantém o Status 'Pendente'.
    """
    video, temporario, sucesso = None, False, False
    erros = []
    redes = redes_do_item(item, redes)
    redes_agora = [r for r in redes if r in PROGRAMAVEIS] if antecipado else redes
    try:
        pendentes = redes_agora if teste else [r for r in redes_agora if not registro.ja_publicado(item.chave, r)]
        if not pendentes:
            log.info("Linha %s: já publicada em %s", item.linha, ", ".join(redes_agora))
        else:
            if not teste and not antecipado:
                planilha.atualizar_status(item, Status.PROCESSANDO)
            video, temporario = videos.obter_video(item)
            for rede in pendentes:
                try:
                    post_id = PUBLICADORES[rede](video, item, teste)
                    if not teste:
                        registro.marcar(item.chave, rede, post_id, item.linha)
                except Exception as e:
                    log.exception("Linha %s: falha no %s", item.linha, rede)
                    erros.append(f"{rede}: {e}")

        if not teste:
            if erros:
                planilha.atualizar_status(item, Status.ERRO, " | ".join(erros))
            elif not antecipado:
                planilha.atualizar_status(item, Status.POSTADO)
        sucesso = not erros
        return sucesso

    except Exception as e:
        log.exception("Linha %s falhou", item.linha)
        if not teste:
            planilha.atualizar_status(item, Status.ERRO, f"{type(e).__name__}: {e}")
        return False
    finally:
        if temporario:
            videos.finalizar(video, True)  # apaga o baixado do Drive
        elif sucesso and not teste and not antecipado and video:
            # só sai da pasta quando todas as redes estiverem feitas
            if all(registro.ja_publicado(item.chave, r) for r in redes):
                videos.finalizar(video, False)


def cmd_publicar(args) -> int:
    redes = [r.strip() for r in (args.redes or config.REDES_ATIVAS).split(",") if r.strip()]
    desconhecidas = set(redes) - PUBLICADORES.keys()
    if desconhecidas:
        log.error("Redes não implementadas: %s. Disponíveis: %s", desconhecidas, list(PUBLICADORES))
        return 2

    planilha = PlanilhaConteudos()
    tarefas: list[tuple[Conteudo, bool]] = []  # (item, antecipado)

    if args.linha:
        item = planilha.obter(args.linha)
        if item.problemas:
            log.error("Linha %s não pode ser publicada — falta: %s", item.linha, ", ".join(item.problemas))
            return 1
        from datetime import datetime
        from services.sheets import TZ
        futuro = item.data_postar_dt and item.data_postar_dt > datetime.now(TZ)
        tarefas.append((item, bool(futuro) and not args.teste and set(redes) <= PROGRAMAVEIS))
    else:
        # 1) o que já chegou na data -> todas as redes
        for item in planilha.buscar_para_publicar():
            tarefas.append((item, False))
        # 2) o que vai ao ar nas próximas horas -> programar nas redes que permitem
        if config.YOUTUBE_ANTECEDENCIA_HORAS > 0 and PROGRAMAVEIS & set(redes):
            for item in planilha.buscar_proximos(config.YOUTUBE_ANTECEDENCIA_HORAS):
                if not all(registro.ja_publicado(item.chave, r) for r in PROGRAMAVEIS & set(redes)):
                    tarefas.append((item, True))
        tarefas = tarefas[: config.MAX_POR_EXECUCAO]

    if not tarefas:
        log.info("Nada para publicar ou programar agora.")
        return 0

    log.info("%s tarefa(s) — redes: %s%s", len(tarefas), ", ".join(redes), " [TESTE]" if args.teste else "")
    ok = 0
    for item, antecipado in tarefas:
        log.info("Linha %s (%s) -> %s", item.linha, item.data_postar,
                 "programar" if antecipado else "publicar")
        ok += publicar_linha(planilha, item, redes, args.teste, antecipado)
    log.info("Fim: %s ok, %s com erro", ok, len(tarefas) - ok)
    return 0 if ok == len(tarefas) else 1


def cmd_contas(_args) -> int:
    contas = instagram.contas_configuradas()
    if not contas:
        print("\nNenhum perfil do Instagram no .env (INSTAGRAM_ID_1 + META_TOKEN_1).\n")
        return 1
    for rede, conta in contas.items():
        try:
            i = instagram.info_conta(conta)
            print(f"{rede}: @{i.get('username')}  seguidores={i.get('followers_count')}  posts={i.get('media_count')}  id={i.get('id')}")
        except Exception as e:
            print(f"{rede}: ERRO — {e}")
    print(f"Redes ativas: {config.REDES_ATIVAS}\n")
    return 0


def cmd_salvar_token(_args) -> int:
    """Lê o token copiado (Ctrl+C / botão Copiar) e grava META_TOKEN no .env, sem mostrar na tela."""
    import re as _re
    import subprocess
    token = ""
    try:
        token = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                               capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        pass
    if not token:
        try:
            import tkinter
            r = tkinter.Tk(); r.withdraw(); token = r.clipboard_get().strip(); r.destroy()
        except Exception:
            pass
    token = (token or "").strip().strip('"').strip("'")
    valido = lambda t: bool(_re.fullmatch(r"EA[A-Za-z0-9_\-]{30,}", t))
    if not valido(token):
        print("Não achei o token na área de transferência.")
        print("1) No Chrome, clique em 'Copiar' na janela do token.")
        print("2) Volte aqui, clique com o BOTÃO DIREITO do mouse nesta janela (isso cola) e aperte Enter.")
        print("   (o token não aparece na tela enquanto você cola — é normal)\n")
        import getpass
        try:
            token = getpass.getpass("Token: ").strip().strip('"').strip("'")
        except Exception:
            token = input("Token: ").strip()
    if not valido(token):
        print(f"\nO texto colado não parece um token da Meta (tamanho {len(token)}, começa com '{token[:2]}').\n")
        return 1
    env = config.BASE_DIR / ".env"
    texto = env.read_text(encoding="utf-8")
    texto = _re.sub(r"^META_TOKEN=.*$", f"META_TOKEN={token}", texto, flags=_re.M)
    env.write_text(texto, encoding="utf-8")
    print(f"\nToken salvo no .env (termina em ...{token[-4:]}). Agora rode: python main.py descobrir-instagram\n")
    return 0


def cmd_descobrir(_args) -> int:
    """Descobre os IDs dos perfis e grava INSTAGRAM_ID_1/2 no .env."""
    if not config.META_TOKEN:
        print("\nMETA_TOKEN vazio no .env. Cole o token do usuário do sistema e rode de novo.\n")
        return 1
    contas = instagram.descobrir_contas(config.META_TOKEN, config.META_BUSINESS_ID)
    if not contas:
        print("\nNenhum perfil encontrado com esse token. Confira se os perfis foram atribuídos ao usuário do sistema.\n")
        return 1
    print("\nPerfis encontrados:")
    for c in contas:
        print(f"  @{c['username']}  id={c['id']}")
    alvo = {"guilhermecristoforeofc": "INSTAGRAM_ID_1", "cortes.guilhermecristofore": "INSTAGRAM_ID_2"}
    env = config.BASE_DIR / ".env"
    texto = env.read_text(encoding="utf-8")
    import re as _re
    for c in contas:
        chave = alvo.get(c["username"].lower())
        if chave:
            texto = _re.sub(rf"^{chave}=.*$", f"{chave}={c['id']}", texto, flags=_re.M)
            print(f"  -> {chave} = {c['id']} (@{c['username']})")
    env.write_text(texto, encoding="utf-8")
    print("\n.env atualizado. Agora rode: python main.py contas\n")
    return 0


def _ler_area_transferencia() -> str:
    import subprocess
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        pass
    try:
        import tkinter
        r = tkinter.Tk(); r.withdraw(); t = r.clipboard_get().strip(); r.destroy(); return t
    except Exception:
        return ""


def cmd_conectar_tiktok(_args) -> int:
    """Abre a autorização do TikTok; depois lê o código copiado e grava o token."""
    import webbrowser
    link = tiktok.link_autorizacao()
    print("\n1) Vai abrir o TikTok no navegador. Confira que é o perfil certo e clique em Autorizar.")
    print("2) Na página do Publicador Forja que aparecer, clique em 'Copiar código'.")
    print("3) Volte aqui e aperte Enter.\n")
    webbrowser.open(link)
    input("Depois de copiar o código, aperte Enter... ")
    codigo = _ler_area_transferencia().strip().strip('"')
    if len(codigo) < 20:
        codigo = input("Não achei o código copiado. Cole aqui (botão direito) e Enter: ").strip()
    tok = tiktok.trocar_codigo(codigo)
    c = tiktok.info_criador()
    print(f"\nTikTok conectado: @{c.get('creator_username')} ({c.get('creator_nickname')})")
    print(f"Privacidade permitida agora: {', '.join(c.get('privacy_level_options') or [])}")
    print(f"Escopos: {tok.get('scope')}\n")
    return 0


def cmd_tiktok(_args) -> int:
    c = tiktok.info_criador()
    print(f"\nTikTok: @{c.get('creator_username')} ({c.get('creator_nickname')})")
    print(f"Privacidade permitida: {', '.join(c.get('privacy_level_options') or [])}")
    print(f"Duração máx. do vídeo: {c.get('max_video_post_duration_sec')}s\n")
    return 0


def cmd_teste_tiktok(_args) -> int:
    """Publica PRIVADO (Somente eu) no TikTok o primeiro vídeo achado na pasta videos (ou videos/postados)."""
    pastas = [config.VIDEOS_DIR, config.VIDEOS_DIR / "postados"]
    video = next((f for d in pastas if d.exists() for f in sorted(d.iterdir())
                  if f.suffix.lower() in (".mp4", ".mov", ".m4v", ".webm")), None)
    if not video:
        print("\nNenhum vídeo na pasta videos. Coloque um .mp4 lá e rode de novo.\n")
        return 1
    print(f"\nEnviando PRIVADO para o TikTok: {video.name}\n(pode levar 1-3 min)\n")
    pid = tiktok.publicar_video(video, "Teste do robô de publicação", privacidade="SELF_ONLY")
    print(f"\nOK! Enviado ({pid}). Confira no app do TikTok > seu perfil > vídeos privados (cadeado).\n")
    return 0


def cmd_kit_tiktok(args) -> int:
    from services import kit_tiktok
    caminho = kit_tiktok.gerar(args.dias)
    print(f"\nKit gerado: {caminho}\n")
    return 0


def cmd_canal(_args) -> int:
    c = youtube.canal_conectado()
    print(f"\nCanal do YouTube: {c['nome']}  (inscritos: {c['inscritos']}, id: {c['id']})")
    print(f"Programar com antecedência: {config.YOUTUBE_ANTECEDENCIA_HORAS:g}h\n")
    return 0


def main() -> int:
    configurar_logs()
    p = argparse.ArgumentParser(description="Robô de publicação de vídeos curtos")
    sub = p.add_subparsers(dest="comando", required=True)

    pp = sub.add_parser("publicar", help="Rodada normal de publicação")
    pp.add_argument("--linha", type=int, help="Processar só esta linha")
    pp.add_argument("--redes", help="Ex.: youtube  (padrão: REDES_ATIVAS do .env)")
    pp.add_argument("--teste", action="store_true", help="Envia como privado sem data e não altera a planilha")
    pp.set_defaults(func=cmd_publicar)

    sub.add_parser("canal", help="Mostra o canal do YouTube conectado").set_defaults(func=cmd_canal)
    pk = sub.add_parser("kit-tiktok", help="Gera a página de agendamento manual do TikTok")
    pk.add_argument("--dias", type=int, help="Quantos dias à frente (padrão TIKTOK_DIAS do .env)")
    pk.set_defaults(func=cmd_kit_tiktok)
    sub.add_parser("conectar-tiktok", help="Autoriza o robô no TikTok").set_defaults(func=cmd_conectar_tiktok)
    sub.add_parser("teste-tiktok", help="Posta um vídeo PRIVADO de teste no TikTok").set_defaults(func=cmd_teste_tiktok)
    sub.add_parser("tiktok", help="Mostra o perfil do TikTok conectado").set_defaults(func=cmd_tiktok)
    sub.add_parser("contas", help="Mostra os perfis do Instagram conectados").set_defaults(func=cmd_contas)
    sub.add_parser("salvar-token", help="Grava no .env o token copiado da Meta").set_defaults(func=cmd_salvar_token)
    sub.add_parser("descobrir-instagram", help="Acha os IDs dos perfis e grava no .env").set_defaults(func=cmd_descobrir)

    args = p.parse_args()
    try:
        return args.func(args)
    except Exception as e:
        log.exception("Comando '%s' falhou", args.comando)
        print(f"\nERRO: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
