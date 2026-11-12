# gemini_reverse_api

Локальный **OpenAI-совместимый API** поверх веб-приложения Gemini
([gemini.google.com](https://gemini.google.com/app)).

Никакого API-ключа и никакой оплаты: прокси говорит с Gemini от имени обычного
Google-аккаунта, в который владелец вошёл в браузере. Сессию держит браузер, прокси
забирает из него куки и ходит в Google напрямую по HTTP (TLS-отпечаток Chrome
имитируется, так что серверный IP — единственное, что отличается от живого браузера).

```
бот / сайт ──► /v1/chat/completions ──► прокси ──► gemini.google.com
                                                  ▲
Chrome (залогинен) ──куки (CDP)──► accounts.json ┘
```

Квота считается на Google-аккаунт и обновляется каждые 5 часов:

| аккаунт | кредитов на 5 часов | на неделю | модели |
| --- | --- | --- | --- |
| без подписки | ~600 | ~12 000 | «Авто» |
| Google AI Pro | 2 400 | 48 384 | flash-lite, 4-hydrogen, pro, carbon |
| Google AI Ultra | больше | больше | все |

Простой текстовый запрос стоит примерно один кредит. Аккаунтов может быть сколько угодно:
прокси ротирует их сам и уважает остаток кредитов.

---

## Содержание

- [Как это работает](#как-это-работает)
- [Что нужно](#что-нужно)
- [Быстрый старт](#быстрый-старт)
- [Вход в аккаунт](#вход-в-аккаунт)
- [Сессия: кто владеет](#сессия-кто-владеет)
- [API](#api)
- [Группы аккаунтов](#группы-аккаунтов)
- [Админка](#админка)
- [Формат файла аккаунтов](#формат-файла-аккаунтов)
- [Установка на сервере](#установка-на-сервере)
- [Настройки](#настройки)
- [Ограничения и риски](#ограничения-и-риски)
- [Разработка](#разработка)

---

## Как это работает

1. **Браузер** (Chrome) залогинен в Google-аккаунт. Он сам обновляет куки — так же, как
   обычный браузер на вашем компьютере.
2. **Сборщик сессии** (`session/gemacc.mjs`) раз в несколько минут забирает из браузера
   cookie jar через Chrome DevTools Protocol и записывает его в файл аккаунтов.
3. **Прокси** (`gemini_proxy/`) читает файл, достаёт из сессии токен доступа (Google
   выдаёт его на странице приложения) и отправляет запросы в `gemini.google.com`
   напрямую — через `curl_cffi`, который имитирует TLS-отпечаток Chrome.
4. **Ротация**: аккаунт, ответивший «лимит исчерпан», уходит в паузу до обновления окна
   кредитов; следующий запрос берёт другой. Аккаунты можно развести по группам: бот пишет
   посты на платном, чат сайта отвечает на бесплатном.

Модели приходят от Google при входе и не зашиты в код: у каждого аккаунта свой список.

## Что нужно

- Linux, **Python 3.11+** (на Ubuntu 22.04 системный 3.10 — возьмите сборку с
  [astral.sh/uv](https://astral.sh/uv/) или поставьте 3.11+ из deadsnakes).
- **Node.js 18+** — только для сборщика сессии.
- **Google Chrome** — он и есть владелец сессии.
- Один или несколько Google-аккаунтов, в которые можно войти.

Зависимости Python: `gemini-webapi` (протокол веб-приложения), `fastapi`, `uvicorn`.
Node-зависимостей нет.

## Быстрый старт

```bash
git clone https://github.com/PRISSET/gemini_reverse_api.git
cd gemini_reverse_api

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Прокси поднимается одной командой:

```bash
ACCOUNTS_FILE=$PWD/accounts.json .venv/bin/python -m uvicorn gemini_proxy.app:app \
  --host 127.0.0.1 --port 3086
```

Проверка:

```bash
curl http://127.0.0.1:3086/health
```

## Вход в аккаунт

Сессию заводит браузер. На своей машине:

```bash
node session/gemacc.mjs login
```

Скрипт откроет Chrome, напечатает ссылку — войдите в Google-аккаунт и подтвердите доступ.
Когда сессия появится, скрипт сам соберёт куки, найдёт аккаунты и запишет их в
`accounts.json`.

Если Chrome уже запущен со скриптом (`node session/gemacc.mjs browser`), вход делается
в его окне, а `gemacc.mjs refresh` просто забирает свежие куки.

### Несколько аккаунтов

В одном профиле Chrome может быть несколько залогиненных Google-аккаунтов — тогда
приложение Gemini различает их по индексу в пути (`/u/0/`, `/u/1/`). Сборщик сам
обходит индексы и записывает все, что подтвердились. Второй аккаунт добавляется через
меню профиля прямо в окне браузера.

## Сессия: кто владеет

Google ротирует cookie раз в час. Кто владеет браузером — тот и держит сессию живой.

**Вариант A — браузер на своём компьютере** (домашний IP, самый «настоящий» отпечаток).
Куки забирает локальный таймер и отправляет на сервер:

```bash
node session/gemacc.mjs refresh
scp accounts.json server:/tmp/accounts.json
ssh server 'install -m 640 -o root -g hfnews /tmp/accounts.json /etc/gemini-proxy/accounts.json'
```

**Вариант B — браузер на сервере** (вариант «поставил и забыл»). Поднимается
screen + VNC, чтобы зайти через браузер:

```bash
sudo bash session/setup.sh
ssh -L 6080:127.0.0.1:6080 user@server
# открыть http://localhost:6080/vnc.html и войти в аккаунт
```

В обоих случаях один аккаунт не должен быть залогинен сразу в двух браузерах: ротация
куков в одном убивает сессию другого.

Если сессия всё же умерла, сборщик уведомит владельца (через Telegram, если настроить
`session/notify.py`) и оставит окно входа открытым.

## API

### `POST /v1/chat/completions`

Обычный OpenAI-формат.

```bash
curl http://127.0.0.1:3086/v1/chat/completions \
  -H 'Authorization: Bearer pool' \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "gemini-4-hydrogen",
        "messages": [
          {"role": "system", "content": "Отвечай кратко на русском."},
          {"role": "user", "content": "Что такое LoRA?"}
        ],
        "temperature": 0.4
      }'
```

```json
{
  "id": "chatcmpl-…", "object": "chat.completion", "created": 1794515968,
  "model": "gemini-4-hydrogen",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "…"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

Поля тела: `model`, `messages`, `temperature`, `max_tokens`, `stream`.
У веб-приложения нет системной роли и JSON-режима: системный промпт и все реплики
склеиваются в один промпт, а строгий JSON приходится просить словами.

Потоковый ответ (`"stream": true`) — обычный SSE:

```
data: {"choices":[{"delta":{"content":"Привет"}}]}
…
data: [DONE]
```

### `GET /v1/models`

Модели, которые предлагает аккаунт (список приходит от Google при входе).

### `GET /health`

Состояние пула: сколько аккаунтов, кто в паузе, кто мёртв.

## Группы аккаунтов

Bearer решает, кто отвечает:

| Bearer | Кто обслуживает |
| --- | --- |
| `pool` | все аккаунты по кругу |
| `pro` | аккаунты с меткой `pro` |
| `a2` | конкретный аккаунт по id |
| `you@gmail.com` | конкретный аккаунт по почте |

Метка задаётся либо в админке, либо из консоли:

```bash
ACCOUNTS_FILE=accounts.json .venv/bin/python -m gemini_proxy label a2 chat
```

Так бот пишет посты на платном аккаунте (`LLM_TOKEN=pro`), а чат сайта отвечает на
бесплатном (`LLM_TOKEN=chat`).

## Админка

Маршруты закрыты заголовком `X-Admin-Key` (значение — `PROXY_ADMIN_KEY`).

| Маршрут | Что делает |
| --- | --- |
| `GET /admin/accounts` | аккаунты: тариф, кредиты, модели, статус |
| `POST /admin/check` | войти заново и прочитать квоту прямо сейчас |
| `PUT /admin/accounts/:id` | переименовать (метка — это группа) |
| `DELETE /admin/accounts/:id` | забыть аккаунт (сессия в браузере остаётся) |

Пример:

```bash
curl -H "X-Admin-Key: $PROXY_ADMIN_KEY" -X POST http://127.0.0.1:3086/admin/check
```

```json
{"accounts": [{
  "id": "c9f3e8ec6e8b732b", "slot": "u/0", "label": "pro",
  "email": "you@gmail.com", "masked": "yo***@gmail.com", "tier": "PRO",
  "models": ["gemini-flash-lite", "gemini-4-hydrogen", "gemini-pro", "gemini-carbon"],
  "status": "alive",
  "cookiesAt": "2026-11-12T20:33:28.000Z",
  "lastOk": "2026-11-12T21:32:55.000Z",
  "lastError": null,
  "total": {"ok": 142, "failed": 0, "limited": 0},
  "quota": {
    "tier": "PRO",
    "window5h": {"window": "5h", "remaining": 2354, "percent": 2, "resetAt": "…"},
    "weekly":  {"window": "weekly", "remaining": 48315, "percent": 0, "resetAt": "…"},
    "models": [{"label": "Gemini Flash", "remaining": 2354, "total": 2400, "percent": 2}]
  }
}]}
```

Статусы: `alive` — работает, `cooldown` — упёрся в лимит, `dead` — сессия слетела,
`unknown` — ещё не проверялся.

## Формат файла аккаунтов

```json
{
  "accounts": [
    { "id": "a1", "authuser": "0", "email": "you@gmail.com", "label": "pro",
      "cookies": { "__Secure-1PSID": "…", "__Secure-1PSIDTS": "…", "SID": "…" },
      "updatedAt": "2026-11-12T20:33:28.000Z" },
    { "id": "a2", "authuser": "1", "email": "free@gmail.com", "label": "chat",
      "cookies": { "…": "…" }, "updatedAt": "2026-11-12T20:33:28.000Z" }
  ]
}
```

`authuser` — индекс из `gemini.google.com/u/N/app`: в одном профиле Chrome может быть
несколько залогиненных аккаунтов, и путь выбирает, с кем говорить.
Файл читает прокси и перезаписывает сборщик сессии — держите права `0640` и группу,
под которой работает прокси.

## Установка на сервере

```bash
sudo bash session/setup.sh
```

Скрипт поставит Chrome, экран, VNC и noVNC, создаст пользователя браузера и
соберёт прокси в `/opt/gemini-proxy`, а юниты поставт в systemd:

| Юнит | Что делает |
| --- | --- |
| `gemini-proxy.service` | сам прокси (127.0.0.1:3086) |
| `gem-xvfb.service` | виртуальный экран `:7` |
| `gem-vnc.service`, `gem-novnc.service` | доступ к экрану по SSH-туннелю |
| `gem-browser.service` | Chrome с сессией — владелец кук |
| `gem-refresh.service` / `.timer` | раз в 10 минут забирать свежие куки |

Дальше:

```bash
sudo systemctl enable --now gem-xvfb gem-vnc gem-novnc gem-browser gemini-proxy
sudo systemctl enable --now gem-refresh.timer
bash session/login.sh        # войти в аккаунт через noVNC
```

## Настройки

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `PROXY_PORT` | `3086` | порт прокси |
| `ACCOUNTS_FILE` | `/etc/gemini-proxy/accounts.json` | файл аккаунтов |
| `PROXY_ADMIN_KEY` | — | ключ админки; без него админских маршрутов нет |
| `GEMINI_DEFAULT_MODEL` | `gemini-4-hydrogen` | модель по умолчанию |
| `GEMINI_TIMEOUT` | `450` | таймаут одного запроса |
| `GEMINI_INIT_TIMEOUT` | `120` | таймаут первого запроса к новому аккаунту |
| `POOL_MAX_RETRIES` | `3` | сколько аккаунтов пробует один запрос |
| `ACCOUNTS_RELOAD_S` | `60` | как часто перечитывать файл аккаунтов |
| `GEMINI_PROXY` | — | SOCKS5/HTTP-прокси для google.com |

Переменные сборщика сессии (`session/gemacc.mjs`):

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `GEMINI_BASE` | `/opt/gemini-proxy` | где лежат профиль и журналы |
| `GEMINI_PROFILE` | `<base>/profile` | профиль Chrome |
| `GEMINI_CDP_PORT` | `9334` | порт отладки Chrome |
| `ACCOUNTS_FILE` | `/etc/gemini-proxy/accounts.json` | куда писать куки |
| `GEMINI_ACCOUNTS` | `4` | сколько индексов u/N проверять |
| `GEMINI_LOG_DIR` | `/var/log/gemini` | журнал браузера |

## Ограничения и риски

- Это **неофициальный** путь: используется внутренний протокол веб-приложения. Google
  может его изменить — тогда прокси придётся обновлять.
- Google может попросить войти заново (2FA, «необычная активность», смена пароля).
  В этом случае владелец заходит через noVNC, дальше всё работает само.
- Аккаунт, которым пользуются с datacenter-IP, рискует попасть под ограничения Google.
  Домашний IP безопаснее — держите браузер на своей машине.
- Не держите один аккаунт одновременно в двух браузерах: ротация куков в одном убивает
  сессию другого.
- Бесплатный аккаунт даёт ~600 кредитов на 5 часов — этого хватает на ответы чата,
  но не на поток постов.

## Разработка

```bash
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests/ -q
```

Тесты покрывают конвертацию сообщений, ротацию аккаунтов, выбор группы по метке,
разбор потока SSE и отказ одного аккаунта без остановки запроса.

---

Протокол веб-приложения реализован библиотекой
[`gemini-webapi`](https://github.com/HanaokaYuzu/Gemini-API) — она разбирает внутренние
RPC Gemini, обновляет токены и умеет мультилогин.
