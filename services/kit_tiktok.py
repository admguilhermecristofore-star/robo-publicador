"""Kit de agendamento do TikTok (agendamento manual pelo TikTok Studio).

Gera uma página HTML com os próximos vídeos 'Pendente' da planilha: data/hora,
legenda com botão "Copiar", caminho do vídeo para colar na janela de upload e
caixinha "Agendado" (fica salva no navegador).
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config
from services.sheets import PlanilhaConteudos, Status, TZ
from services.videos import encontrar_local

log = logging.getLogger(__name__)

LIMITE_LEGENDA_TIKTOK = 4000
DIAS_SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
ARQUIVO_SAIDA = config.BASE_DIR.parent / "TIKTOK - AGENDAR.html"


def _itens(dias: int) -> list[dict]:
    agora = datetime.now(TZ)
    limite = agora + timedelta(days=dias)
    itens = []
    for c in PlanilhaConteudos().listar(Status.PENDENTE, Status.AGENDADO):
        data = c.data_postar_dt
        if data is None or not (agora - timedelta(hours=1) <= data <= limite):
            continue
        video = encontrar_local(c.nome_arquivo) if c.nome_arquivo else None
        avisos = []
        if not video:
            avisos.append("vídeo não encontrado na pasta 'videos'"
                          + (" (há link do Drive)" if c.drive_file_id else ""))
        legenda = c.copy.strip()
        if not legenda or legenda.startswith("#") and len(legenda) < 15:
            avisos.append("copy (coluna D) vazia ou com erro")
        itens.append({
            "chave": c.chave, "linha": c.linha, "data": data,
            "titulo": c.titulo or c.nome_arquivo or "(sem título)",
            "legenda": legenda[:LIMITE_LEGENDA_TIKTOK],
            "video": str(_caminho_windows(video)) if video else "",
            "drive": c.link_drive, "avisos": avisos,
        })
    return sorted(itens, key=lambda i: i["data"])


def _caminho_windows(p: Path) -> str:
    """No PC o caminho já é Windows; mantém como está."""
    return str(p)


def gerar(dias: int | None = None) -> Path:
    dias = dias or config.TIKTOK_DIAS
    itens = _itens(dias)
    cards = []
    dia_atual = None
    for i in itens:
        d = i["data"]
        rotulo_dia = f"{DIAS_SEMANA[d.weekday()]}, {d:%d/%m}"
        if rotulo_dia != dia_atual:
            cards.append(f'<h2>{html.escape(rotulo_dia)}</h2>')
            dia_atual = rotulo_dia
        avisos = "".join(f'<div class="aviso">⚠ {html.escape(a)}</div>' for a in i["avisos"])
        botao_video = (f'<button data-copy="{html.escape(i["video"], quote=True)}">Copiar caminho do vídeo</button>'
                       if i["video"] else
                       (f'<a class="btn" href="{html.escape(i["drive"], quote=True)}" target="_blank">Abrir no Drive</a>' if i["drive"] else ""))
        cards.append(f'''
<article class="card" data-chave="{html.escape(i["chave"], quote=True)}">
  <header>
    <span class="hora">{d:%H:%M}</span>
    <span class="titulo">{html.escape(i["titulo"])}</span>
    <label class="feito"><input type="checkbox"> Agendado</label>
  </header>
  <div class="meta">Linha {i["linha"]} · {d:%d/%m/%Y} às {d:%H:%M}{' · ' + html.escape(Path(i["video"]).name) if i["video"] else ''}</div>
  {avisos}
  <pre class="legenda">{html.escape(i["legenda"]) or "(sem legenda)"}</pre>
  <div class="acoes">
    <button data-copy="{html.escape(i["legenda"], quote=True)}">Copiar legenda</button>
    {botao_video}
    <button data-copy="{d:%d/%m/%Y %H:%M}">Copiar data/hora</button>
  </div>
</article>''')

    corpo = "\n".join(cards) if cards else '<p class="vazio">Nenhum vídeo Pendente nos próximos dias.</p>'
    gerado = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")
    pagina = f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agendar TikTok</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#1b1b1b;--mut:#666;--bd:#ddd;--ac:#fe2c55;--ok:#1a7f37;--warn:#9a6700}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1b1b1b;--fg:#eee;--mut:#aaa;--bd:#333;--ok:#3fb950;--warn:#d29922}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif}}
main{{max-width:860px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:24px;margin:0 0 4px}}h2{{font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:28px 0 8px}}
.topo{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0 4px}}
.passos{{color:var(--mut);font-size:14px;margin:8px 0 0;padding-left:18px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px 16px;margin:10px 0}}
.card header{{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}
.hora{{font-weight:700;font-size:18px;color:var(--ac)}}.titulo{{font-weight:600;flex:1;min-width:200px}}
.feito{{font-size:14px;color:var(--mut);cursor:pointer;white-space:nowrap}}
.meta{{color:var(--mut);font-size:13px;margin-top:2px}}
.legenda{{white-space:pre-wrap;font:inherit;font-size:14px;background:var(--bg);border-radius:8px;padding:10px 12px;margin:10px 0;max-height:160px;overflow:auto}}
.acoes{{display:flex;flex-wrap:wrap;gap:8px}}
button,.btn{{font:inherit;font-size:14px;padding:7px 12px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none}}
button:hover,.btn:hover{{border-color:var(--ac)}}.principal{{background:var(--ac);color:#fff;border-color:var(--ac)}}
.copiado{{border-color:var(--ok)!important;color:var(--ok)}}
.aviso{{color:var(--warn);font-size:13px;margin-top:6px}}
.card.ok{{opacity:.45}}.card.ok .hora{{color:var(--ok)}}
body.esconder .card.ok{{display:none}}
.vazio{{color:var(--mut)}}
</style></head><body><main>
<h1>Agendar no TikTok</h1>
<div class="meta">Próximos {dias} dias · {len(itens)} vídeo(s) · gerado em {gerado}</div>
<div class="topo">
  <a class="btn principal" href="https://www.tiktok.com/tiktokstudio/upload" target="_blank">Abrir TikTok Studio ↗</a>
  <button id="esconder">Esconder agendados</button>
</div>
<ol class="passos">
  <li>No TikTok Studio, clique em <b>Selecionar vídeo</b>. Aqui, clique em <b>Copiar caminho do vídeo</b> e, na janela do Windows, cole (Ctrl+V) no campo "Nome" e dê Enter.</li>
  <li><b>Copiar legenda</b> → cole na descrição.</li>
  <li>Em "Quando publicar", escolha <b>Programar</b> com a data/hora do card → <b>Programar</b>.</li>
  <li>Marque <b>Agendado</b> aqui.</li>
</ol>
{corpo}
</main>
<script>
const K="tiktok-agendados";
function ler(){{try{{return JSON.parse(localStorage.getItem(K)||"{{}}")}}catch(e){{return {{}}}}}}
function salvar(d){{try{{localStorage.setItem(K,JSON.stringify(d))}}catch(e){{}}}}
const feitos=ler();
document.querySelectorAll(".card").forEach(c=>{{
  const cb=c.querySelector(".feito input"), k=c.dataset.chave;
  cb.checked=!!feitos[k]; c.classList.toggle("ok",cb.checked);
  cb.addEventListener("change",()=>{{const d=ler(); if(cb.checked)d[k]=Date.now(); else delete d[k]; salvar(d); c.classList.toggle("ok",cb.checked);}});
}});
document.addEventListener("click",async e=>{{
  const b=e.target.closest("[data-copy]"); if(!b)return;
  const t=b.dataset.copy;
  try{{await navigator.clipboard.writeText(t)}}catch(_){{const a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();document.execCommand("copy");a.remove()}}
  const o=b.textContent; b.textContent="Copiado ✓"; b.classList.add("copiado");
  setTimeout(()=>{{b.textContent=o;b.classList.remove("copiado")}},1500);
}});
document.getElementById("esconder").addEventListener("click",ev=>{{
  document.body.classList.toggle("esconder");
  ev.target.textContent=document.body.classList.contains("esconder")?"Mostrar agendados":"Esconder agendados";
}});
</script></body></html>'''
    ARQUIVO_SAIDA.write_text(pagina, encoding="utf-8")
    log.info("Kit do TikTok gerado: %s (%s vídeos)", ARQUIVO_SAIDA.name, len(itens))
    return ARQUIVO_SAIDA
