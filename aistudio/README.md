# aistudio — AI Studio как reverse proxy

Модуль к [gemini_reverse_api](../README.md): тот же подход (сессия Google-аккаунта, без
API-ключа), но для **https://aistudio.google.com/**.

AI Studio — это не веб-приложение Gemini: у него свой бэкенд и свой протокол. Здесь разобрано
всё, что нужно, чтобы говорить с ним из своего кода.

---

## Что внутри

| файл | что делает |
| --- | --- |
| `rpc.py` | прямые RPC AI Studio: список моделей, тариф, токен, подсчёт токенов |
| `cdp.py` | маленький клиент Chrome DevTools Protocol (только то, что нужно) |
| `session.py` | генерация через браузер: выбрать модель, задать промпт, нажать Run, забрать ответ |
| `models.py` | выбор модели по имени среди тех, что есть у аккаунта |
| `cookies.py` | куки аккаунта — из браузера, live |
| `openai.py` | разбор сообщений OpenAI: текст в промпт, картинки в файлы |
| `server.py` | OpenAI-совместимый API (`/v1/chat/completions`, `/v1/models`, `/health`) |
| `cli.py` | `check`, `ask`, `serve` |

---

## Протокол

AI Studio говорит через gapi (protobuf-JSON поверх HTTP), а не через batchexecute, как
веб-приложение Gemini:

```
POST https://alkalimakersuite-pa.clients6.google.com/$rpc/
     google.internal.alkali.applications.makersuite.v1.MakerSuiteService/<Method>
```

Заголовки:

```
Authorization: SAPISIDHASH <ts>_<sha1(ts sapisid origin)> SAPISID1PHASH … SAPISID3PHASH …
X-Goog-Api-Key: AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs   (публичный ключ страницы)
X-Goog-AuthUser: 0
Content-Type: application/json+protobuf
Referer: https://aistudio.google.com/
```

Тело — protobuf-JSON, например `["users/me"]`.

**Что работает из скрипта** (только куки + подпись):

| Метод | что отдаёт |
| --- | --- |
| `ListModels` | 41 модель с контекстом и описанием |
| `GetAiStudioBenefitTier` | тариф аккаунта |
| `GenerateAccessToken` | OAuth-токен аккаунта (его скоуп только на AI Studio, публичный API он не открывает) |
| `CountTokens` | сколько токенов съест промпт |

**Что не работает из скрипта**: `GenerateContent`. Запрос подписывается блобом, который
умеет делать только JavaScript самой страницы (зашифрованная полезная нагрузка в теле).
Поэтому генерация идёт через браузер: вкладка получает промпт, жмёт Run, ответ читается
из чата. Это честный reverse, без ключей и без API.

---

## Как это работает

```
браузер (залогинен в aistudio.google.com)
   │  куки live через CDP + вкладка, в которой идёт запрос
   ▼
aistudio/session.py ──► страница подписывает запрос сама
   │
   ▼
aistudio/rpc.py ──────► список моделей, тариф, токены (быстро, без страницы)
```

Куки читаются **из браузера в момент запроса**: Google ротирует `SAPISID`, и подпись
SAPISIDHASH, посчитанная по старой куке, не проходит. Файл `accounts.json` — только
запасной вариант.

---

## Требования

- Linux, **Python 3.11+**.
- **Chrome**, залогиненный в `https://aistudio.google.com/` (тот же, что держит сессию
  для основного прокси), запущенный с портом отладки:

  ```bash
  google-chrome-stable --remote-debugging-port=9337 --user-data-dir=/path/to/profile
  ```

  Юнит `gem-browser.service` из основного проекта запускает именно так.
- Зависимости: `websockets`, `httpx`, `curl-cffi` (уже стоят в `requirements.txt`
  основного проекта).

---

## Использование

### Список моделей и тариф

```bash
python -m aistudio check
```

```
аккаунт: nazar.mix24052007@gmail.com (a1, u/0)
тариф: 1
  antigravity-preview-09-2026        Antigravity Agent Preview   контекст 1048576
  gemini-4-hydrogen                  Gemini 4 Hydrogen           контекст 1048576
  gemini-4-carbon                    Gemini 4 Carbon             контекст 1048576
  gemini-4-argon                     Gemini 4 Argon              контекст 1048576
  gemini-3.8-flash                   Gemini 3.8 Flash            контекст 1048576
  gemini-3.5-flash-lite              Gemini 3.5 Flash Lite       контекст 1048576
  gemini-3.1-pro-preview             Gemini 3.1 Pro Preview      контекст 1048576
  …
```

### Один запрос

```bash
python -m aistudio ask "Ответь одним словом: сколько будет два плюс два?" gemini-3.8-flash
```

```
модель: gemini-3.8-flash
Четыре
```

### Как сервер

```bash
ACCOUNTS_FILE=/etc/gemini-proxy/accounts.json \
AISTUDIO_CDP_PORT=9334 AISTUDIO_PORT=3087 \
python -m aistudio serve
```

```bash
curl http://127.0.0.1:3087/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemini-3.5-flash-lite","messages":[{"role":"user","content":"Привет"}]}'
```

```json
{"id":"chatcmpl-…","object":"chat.completion","model":"gemini-3.5-flash-lite",
 "choices":[{"index":0,"message":{"role":"assistant","content":"Привет!"},"finish_reason":"stop"}]}
```

---

## Настройки

| Переменная | По умолчанию | Что делает |
| --- | --- | --- |
| `ACCOUNTS_FILE` | `/etc/gemini-proxy/accounts.json` | файл аккаунтов (запасной источник кук) |
| `AISTUDIO_CDP_HOST` | `127.0.0.1` | хост браузера с отладкой |
| `AISTUDIO_CDP_PORT` | `9337` | порт отладки Chrome |
| `AISTUDIO_ACCOUNT` | — | какой аккаунт использовать: id (`a1`), почта или метка |
| `AISTUDIO_DEFAULT_MODEL` | `gemini-flash` | модель, когда вызывающий не назвал |
| `AISTUDIO_TIMEOUT` | `180` | сколько ждать ответа |
| `AISTUDIO_PORT` | `3087` | порт серверного режима |

---

## Несколько аккаунтов

Да, поддерживается. В одном профиле Chrome может быть несколько залогиненных Google-аккаунтов
(мультилогин), и приложение различает их индексом в пути: `gemini.google.com/u/0/…`,
`/u/1/…`. У AI Studio так же — вкладка открывается по адресу
`https://aistudio.google.com/u/N/prompts/new_chat?model=…`, и N выбирает аккаунт.

Куки при этом общие для всех аккаунтов (один cookie jar на профиль), так что подписи
SAPISIDHASH годятся для любого из них.

Как добавить второй аккаунт локально:

```bash
# 1. в браузере с профилем прокси: меню профиля → добавить аккаунт
GEMINI_BROWSER_USER= node session/gemacc.mjs browser
# (откроется Chrome, войдите вторым аккаунтом)

# 2. собрать аккаунты и записать индексы
ACCOUNTS_FILE=accounts.json node session/gemacc.mjs refresh
```

`gemacc.mjs` сам обходит индексы `/u/N/` и записывает найденные аккаунты в `accounts.json`
с их `authuser`.

Кто отвечает на запрос:

| Bearer | Кто обслуживает |
| --- | --- |
| (нет) | аккаунты по кругу |
| `a2` | аккаунт с id `a2` |
| `free@gmail.com` | аккаунт с такой почтой |
| `chat` | аккаунт с такой меткой |

```bash
# по кругу
curl http://127.0.0.1:3087/v1/chat/completions -d '…'
# конкретный аккаунт
curl http://127.0.0.1:3087/v1/chat/completions -H 'Authorization: Bearer a2' -d '…'
```

Вкладка заводится на каждую пару «аккаунт + модель» и живёт между запросами.

---

## Картинки

Да, умеет. Формат как у OpenAI — картинка в `image_url` отдельной частью сообщения:

```bash
python3 - <<'PY' > /tmp/req.json
import base64, json
data = base64.b64encode(open("photo.jpg", "rb").read()).decode()
print(json.dumps({"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": [
    {"type": "text", "text": "Что на картинке?"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}]}]}))
PY

curl http://127.0.0.1:3087/v1/chat/completions \
  -H 'Content-Type: application/json' -d @/tmp/req.json
```

```
{"choices":[{"message":{"role":"assistant","content":"GEMINI 42 TEST"}}]}
```

Как это устроено: картинка пишется во временный файл, файл подставляется в
`<input type="file">` страницы через CDP (`DOM.setFileInputFiles`), дальше страница сама
грузит файл и отправляет его модели вместе с промптом. Модель видит картинку так же, как
человек в интерфейсе.

Из командной строки:

```bash
python -m aistudio ask "Что на картинке?" gemini-3.8-flash photo.jpg
```

Ограничения: файлы отправляются один за другим, каждый ждёт загрузки; ограничение на
размер и тип задаёт сам AI Studio; в одном запросе разумно держать одну-две картинки.

---

## Ограничения

- **Медленнее обычного API**: ответ идёт через страницу (вкладка, ввод, Run, разбор
  ответа) — единицы секунд на запрос вместо сотен миллисекунд. Зато квота аккаунта
  тратится ровно так же, как если бы человек писал в интерфейсе.
- **Одна вкладка отвечает на один вопрос за раз.** Модуль держит вкладку на модель и
  блокирует её на время запроса (`asyncio.Lock`).
- **Потокового ответа нет**: страница отдаёт ответ целиком, когда он готов.
- **Промпт без разметки**: системный промпт и реплики склеиваются в один текст (у AI
  Studio нет отдельной системной роли).
- **Требуется живой браузер**: без него модуль не работает. Это цена подписи запроса.
- Если браузер закрыт или сессия слетела — AI Studio попросит войти заново.

---

## Тесты

```bash
.venv/bin/python -m pytest aistudio/tests -q
```

Покрыты: выбор модели (точное имя, часть имени, по ключевому слову, запасной вариант),
подпись SAPISIDHASH, чистка заголовка и «мыслей» из ответа.
