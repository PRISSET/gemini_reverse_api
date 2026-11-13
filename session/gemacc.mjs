#!/usr/bin/env node
// The Gemini web sessions.
//
// The browser owns them: it stays signed in to gemini.google.com and keeps the cookies
// fresh. This script only harvests them (CDP) and writes them where the proxy reads them.
//
//   node gemacc.mjs login     open the browser (noVNC), sign in, harvest the accounts
//   node gemacc.mjs refresh   re-read the cookies of the running browser (the timer calls it)
//   node gemacc.mjs check     what is in the accounts file
//
// The accounts file:
//   { "accounts": [ { "id": "a1", "authuser": "0", "email": "you@gmail.com",
//                     "label": "Pro", "cookies": { "__Secure-1PSID": "…" },
//                     "updatedAt": "…" } ] }
// `authuser` is the N of gemini.google.com/u/N/app: one browser profile can hold several
// signed-in Google accounts, and the index says which one the proxy is talking to.
import fs from 'node:fs';
import https from 'node:https';
import { execFileSync, spawn } from 'node:child_process';
import { openSync } from 'node:fs';

const BASE = process.env.GEMINI_BASE || '/opt/gemini-proxy';
const ACC = process.env.ACCOUNTS_FILE || '/etc/gemini-proxy/accounts.json';
const PROFILE = process.env.GEMINI_PROFILE || `${BASE}/profile`;
const DISPLAY = process.env.GEMINI_DISPLAY || ':7';
const CDP_PORT = parseInt(process.env.GEMINI_CDP_PORT || '9334', 10);
const HOST = 'gemini.google.com';
const UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36';
const ACCOUNTS_TO_PROBE = parseInt(process.env.GEMINI_ACCOUNTS || '4', 10);
// How often the owner is told that the session died (six hours).
const NOTIFY_EVERY_MS = 6 * 3600_000;
const LOG_DIR = process.env.GEMINI_LOG_DIR || '/var/log/gemini';
const STAMP = `${LOG_DIR}/.notified`;

/** A file we can write: the log directory when it exists, /tmp otherwise. */
function writable(name) {
  for (const dir of [LOG_DIR, '/tmp']) {
    try {
      fs.accessSync(dir, fs.constants.W_OK);
      return `${dir}/${name}`;
    } catch {
      /* try the next one */
    }
  }
  return `/tmp/${name}`;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// --------------------------------------------------------------------- the file
function readAccounts() {
  try {
    return JSON.parse(fs.readFileSync(ACC, 'utf8')).accounts || [];
  } catch {
    return [];
  }
}

function writeAccounts(accounts) {
  const tmp = `${ACC}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify({ accounts }, null, 2) + '\n', { mode: 0o640 });
  // The proxy runs as another user: the file has to stay readable for it.
  try {
    const gid = Number(execFileSync('id', ['-g', 'hfnews']).toString().trim());
    fs.chownSync(tmp, 0, gid);
  } catch {
    /* the group is unknown here: the file stays as it is */
  }
  fs.renameSync(tmp, ACC);
}

/** The account's own e-mail. The page carries Google's own support addresses too. */
const emailOf = (html) => {
  const labeled = /(?:Google Account|Аккаунт Google)[^"']{0,80}?\(([\w.+-]+@[\w.-]+)\)/i.exec(html);
  if (labeled) return labeled[1];
  const all = html.match(/[\w.+-]+@[\w-]+\.[\w.]{2,}/g) || [];
  const own = all.find((e) => !e.toLowerCase().endsWith('google.com'));
  return own || '';
};

/** When the owner was last told that the session is gone. */
const readStamp = () => {
  try {
    return Number(fs.readFileSync(STAMP, 'utf8').trim()) || 0;
  } catch {
    return 0;
  }
};
const writeStamp = () => {
  try {
    fs.writeFileSync(STAMP, String(Date.now()));
  } catch {
    /* the stamp is only a throttle */
  }
};

/** The cookies that identify the session. The full jar is too long for a header. */
const AUTH_COOKIES = [
  '__Secure-1PSID', '__Secure-1PSIDTS', '__Secure-3PSID', '__Secure-3PSIDTS',
  '__Host-3PSID', '__Host-1PSID', 'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'NID', 'OSID',
];

/**
 * GET gemini.google.com/app (u/0) or /u/N/app with the cookie jar: is that account signed in?
 *
 * A signed-in answer carries a lot of Set-Cookie headers, and Node's default limit is 16 KB:
 * without a bigger one the answer is a "Headers Overflow Error".
 */
function probeAccount(authuser, jar) {
  return new Promise((resolve) => {
    const cookie = AUTH_COOKIES.filter((k) => jar[k])
      .map((k) => `${k}=${jar[k]}`)
      .join('; ');
    const req = https.request(
      {
        host: HOST,
        // Google now redirects /u/0/app to /app: the default account answers only there.
        path: Number(authuser) === 0 ? '/app' : `/u/${authuser}/app`,
        method: 'GET',
        maxHeaderSize: 256 * 1024,
        headers: {
          'User-Agent': UA,
          'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
          Cookie: cookie,
        },
        timeout: 30_000,
      },
      (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
          const email = emailOf(body);
          resolve({ ok: res.statusCode === 200 && !!email, email, status: res.statusCode });
        });
      },
    );
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve({ ok: false, email: '', status: 0, error: e.message }));
    req.end();
  });
}

// --------------------------------------------------------------------- the browser
/** Minimal flat-session CDP client over Node's built-in WebSocket. */
class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.wait = new Map();
    this.handlers = [];
    this.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.id && this.wait.has(msg.id)) {
        const { ok, fail } = this.wait.get(msg.id);
        this.wait.delete(msg.id);
        msg.error ? fail(new Error(msg.error.message)) : ok(msg.result);
      } else if (msg.method) this.handlers.forEach((h) => h(msg));
    };
    this.open = new Promise((r, j) => {
      this.ws.onopen = r;
      this.ws.onerror = j;
    });
    this.closed = new Promise((r) => (this.ws.onclose = r));
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    return new Promise((ok, fail) => this.wait.set(id, { ok, fail }));
  }
}

async function waitCdp() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      return (await r.json()).webSocketDebuggerUrl;
    } catch {
      await sleep(500);
    }
  }
  throw new Error('chrome did not start');
}

function startChrome() {
  const user = process.env.GEMINI_BROWSER_USER || '';
  const browserArgs = [
    'google-chrome-stable',
    `--user-data-dir=${PROFILE}`,
    `--remote-debugging-port=${CDP_PORT}`,
    '--lang=ru', '--accept-lang=ru,en-US,en',
    '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--no-first-run', '--no-default-browser-check', '--password-store=basic',
    '--window-position=0,0', '--window-size=1920,1080', '--start-maximized',
    '--renderer-process-limit=3',
    `https://${HOST}/app`,
  ];
  const env = { ...process.env, DISPLAY, TZ: 'Europe/Berlin', LANG: 'ru_RU.UTF-8' };
  // The browser's own log: a failure to start is otherwise invisible.
  const logFile = writable('chrome.log');
  const out = openSync(logFile, 'a');
  // On the server the browser runs as its own user; locally it runs as the owner.
  if (user) {
    return spawn('runuser', ['-u', user, '--', 'env', `DISPLAY=${DISPLAY}`, ...browserArgs],
      { stdio: ['ignore', out, out] });
  }
  return spawn(browserArgs[0], browserArgs.slice(1), { stdio: ['ignore', out, out], env });
}

/**
 * The cookie jar the browser would send to gemini.google.com.
 *
 * Only .google.com and gemini.google.com cookies: accounts.google.com and the other
 * subdomains hold cookies with the same names (OSID, __Secure-OSID, …) and, merged by
 * name, they overwrite the right ones — Google then answers with a redirect to the login.
 * When a name exists on both, the narrower gemini.google.com cookie wins, as in the browser.
 */
async function harvest(cdp) {
  const { cookies } = await cdp.send('Storage.getCookies');
  const jar = {};
  for (const c of cookies || []) {
    const domain = (c.domain || '').replace(/^\./, '');
    if (domain !== 'google.com' && domain !== HOST) continue;
    if ((c.path || '/') !== '/') continue;
    if (domain !== HOST && c.name in jar) continue;
    jar[c.name] = c.value;
  }
  return jar;
}

/** The accounts: every u/N that answers with a signed-in page. */
async function accountsOf(jar) {
  const found = [];
  for (let n = 0; n < ACCOUNTS_TO_PROBE; n++) {
    const r = await probeAccount(n, jar);
    if (r.ok) {
      found.push({ authuser: String(n), email: r.email });
      log(`u/${n} → ${r.email}`);
    } else {
      log(`u/${n} → нет (${r.status || r.error || '?'})`);
    }
  }
  return found;
}

// --------------------------------------------------------------------- commands
/** The browser, if it is already running (the service keeps it alive). */
async function cdpIfRunning() {
  try {
    const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
    return (await r.json()).webSocketDebuggerUrl;
  } catch {
    return null;
  }
}

async function login() {
  let url = await cdpIfRunning();
  if (!url) {
    log('запускаю браузер, войди в Google-аккаунты и вернись сюда');
    startChrome();
    url = await waitCdp();
  } else {
    log('браузер уже запущен, войди в Google-аккаунты в открывшемся окне');
  }
  const cdp = new Cdp(url);
  await cdp.open;
  log('браузер открыт: войди в аккаунт (второй — через переключатель профиля Google)');

  // The login is a human job: wait until the page shows a signed-in account.
  const deadline = Date.now() + 30 * 60_000;
  let jar = {};
  while (Date.now() < deadline) {
    await sleep(5000);
    try {
      jar = await harvest(cdp);
    } catch {
      continue;
    }
    if (jar['__Secure-1PSID']) break;
  }
  if (!jar['__Secure-1PSID']) {
    log('не дождался входа');
    return 1;
  }
  log('cookies получены, ищу аккаунты...');
  const found = await accountsOf(jar);
  if (!found.length) {
    log('ни один аккаунт не подтвердился');
    return 1;
  }

  const old = readAccounts();
  const accounts = mergeAccounts(old, found, jar);
  writeAccounts(accounts);
  log(`готово: ${accounts.length} аккаунт(ов) → ${ACC}`);
  return 0;
}

/**
 * The accounts file from what the browser holds: the accounts it confirms, with the names
 * they already had, plus the ones that were in the file but could not be checked now.
 */
function mergeAccounts(old, found, jar) {
  const accounts = found.map((f) => {
    const prev = old.find((a) => a.authuser === f.authuser);
    return {
      id: prev?.id || `a${parseInt(f.authuser, 10) + 1}`,
      authuser: f.authuser,
      email: f.email,
      label: prev?.label || '',
      cookies: jar,
      updatedAt: new Date().toISOString(),
    };
  });
  // An account the browser no longer holds is gone; one we could not check stays.
  for (const a of old) {
    if (accounts.some((x) => x.authuser === a.authuser)) continue;
    accounts.push(a);
  }
  return accounts;
}

/** Message to the owner's Saved Messages through the bot's Telegram account. */
function notify(text) {
  try {
    execFileSync(
      '/opt/hfnews/.venv/bin/python',
      ['-I', `${BASE}/notify.py`, text],
      { timeout: 60_000 },
    );
  } catch (e) {
    log('telegram notify failed:', String(e.message || e).split('\n')[0]);
  }
}

async function refresh() {
  let cdp;
  try {
    cdp = new Cdp(await waitCdp());
    await cdp.open;
  } catch {
    log('браузер не запущен, пропускаю');
    return 1;
  }
  let jar;
  try {
    jar = await harvest(cdp);
  } catch (e) {
    log('не смог прочитать куки:', e.message);
    return 1;
  }
  if (!jar['__Secure-1PSID']) {
    log('в браузере нет сессии Google');
    // The session died: the owner has to sign in again. Tell them where the screen is.
    const stamp = readStamp();
    if (Date.now() - stamp > NOTIFY_EVERY_MS) {
      writeStamp();
      notify('Gemini: сессия в браузере слетела, нужно войти заново.\n' +
        'Окно входа: ssh -L 6080:127.0.0.1:6080 root@144.31.148.243 → http://localhost:6080/vnc.html\n' +
        'После входа куки подхватятся сами (refresh раз в 10 минут).');
    }
    return 1;
  }
  const old = readAccounts();
  const found = await accountsOf(jar);
  if (!found.length) {
    log('ни один аккаунт не подтверждается');
    return 1;
  }
  const accounts = mergeAccounts(old, found, jar);
  writeAccounts(accounts);
  log(`обновил ${accounts.length} аккаунт(ов)`);
  return 0;
}

function check() {
  const accounts = readAccounts();
  if (!accounts.length) {
    log('аккаунтов нет');
    return 1;
  }
  for (const a of accounts) {
    const psid = a.cookies?.['__Secure-1PSID'] ? 'есть' : 'НЕТ';
    log(`${a.id} u/${a.authuser} ${a.email || ''} · куки ${psid} · ${a.updatedAt || 'никогда'}`);
  }
  return 0;
}

/** Just the browser, in the foreground: the systemd service runs this. */
async function browser() {
  log(`браузер на ${DISPLAY}, профиль ${PROFILE}, отладка на порту ${CDP_PORT}`);
  startChrome();
  // Keep the event loop busy, or node exits on the pending promise below.
  await new Promise(() => setInterval(() => {}, 1 << 30));
  return 0;
}

const [cmd] = process.argv.slice(2);
const run = { login, refresh, check, browser }[cmd];
if (!run) {
  console.log('usage: gemacc.mjs login | refresh | check | browser');
  process.exit(2);
}
process.exit((await run()) || 0);
