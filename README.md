# Robô de publicação (privado)

Lê a aba **Conteúdos** da planilha e publica os vídeos na Data Postar (Instagram, YouTube, TikTok) pelas APIs oficiais.

- Roda sozinho a cada 30 min pelo GitHub Actions **quando a variável `ROBO_ATIVO` = `sim`**.
- Rodar na mão: aba **Actions → Robô de publicação → Run workflow** (`fila` só lê a planilha; `publicar` publica).
- Senhas ficam em **Settings → Secrets and variables → Actions** (`GOOGLE_TOKEN_JSON`, `META_TOKEN`); IDs em **Variables** (`SPREADSHEET_ID`, `INSTAGRAM_ID_1`, `INSTAGRAM_ID_2`, `META_BUSINESS_ID`, `ROBO_ATIVO`). Nunca colocar senha nos arquivos.
