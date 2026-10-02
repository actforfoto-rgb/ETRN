# ETRN Help Site

Эта папка содержит пакет опубликованного сайта «База помощи при работе с ЭТрН».

Cloudflare Pages настройки:
- Production branch: `main`
- Root directory: оставить пустым (корень репозитория)
- Framework preset: `None`
- Build command: `bash website/build.sh`
- Build output directory: `dist`

Файл `website/site.zip` — базовая версия сайта. Файлы из `website-overrides/` при сборке накладываются поверх неё, поэтому дальнейшие текстовые и кодовые обновления можно вносить напрямую через GitHub.
