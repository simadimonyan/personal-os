#!/usr/bin/env python3
"""Stack Overflow for Agents (SOFA) — тонкий CLI поверх JSON API.

Зачем: SOFA требует Bearer-ключ + X-Sofa-Session на каждом вызове и «сначала прочитай,
потом голосуй». Собирать это руками в каждом агенте — источник ошибок, поэтому вся
механика здесь, а агенты вызывают команды.

Ключ берётся из .sofa/credentials.json (по умолчанию) или из SOFA_API_KEY.
Сессия — рантайм-состояние, кэшируется в /tmp (НЕ в credentials.json, как требует skill.md).

Команды:
  session                        — создать/переиспользовать сессию, напечатать статус
  guidance                       — свежесть установленного skill.md
  me                             — агенты владельца (privileges, publication_policy)
  attention                      — лента «что требует внимания»
  search <запрос> [--tag t] [--type question|til|blueprint|playbook]
                                   [--min-trust N] [--sort trust|newest|hot|relevance] [--limit N]
  get <post_id>                  — полный пост с ответами (обязательно до vote/verify)
  tags                           — список тегов
  playbooks [запрос] [--tag t]   — поиск плейбуков
  playbook <id>                  — карточка плейбука без шагов
  pull <id>                      — вытянуть шаги плейбука (осознанно!)
  vote <post_id> <1|-1>          — голос по прочитанному
  verify <post_id> <worked_as_written|worked_with_changes|did_not_work> <текст>
  post <файл.json>               — создать пост (content_type/title/body/tags)
  reply <post_id> <файл.md>      — ответ в тред
  playbook-publish <файл.json>   — опубликовать плейбук
  my-posts                       — свои посты и черновики (в т.ч. для сверки после обрыва)
  summary                        — сводка активности текущей сессии
  close                          — закрыть сессию

Политика публикации агента `organon` — draft_directly: посты, ответы и плейбуки
создаются черновиками, публикует их человек из дашборда SOFA.
"""
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SITE = os.environ.get("SOFA_SITE", "https://agents.stackoverflow.com")
PROJ = pathlib.Path(__file__).resolve().parents[2]
CRED_PATH = pathlib.Path(os.environ.get("SOFA_CREDENTIALS", PROJ / ".sofa" / "credentials.json"))
SESSION_CACHE = pathlib.Path(f"/tmp/sofa-session-{os.getuid()}.json")
DIGEST_PATH = pathlib.Path(os.environ.get("SOFA_DIGEST_FILE", PROJ / ".sofa" / "skill-digest"))

CLIENT_NAME = os.environ.get("SOFA_CLIENT_NAME", "claude-code")
MODEL_NAME = os.environ.get("SOFA_MODEL_NAME", "claude-opus-5")


def die(msg, code=1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def load_key():
    key = os.environ.get("SOFA_API_KEY")
    if key:
        return key
    if not CRED_PATH.exists():
        die(f"нет ключа: ни SOFA_API_KEY, ни {CRED_PATH}. Пройди онбординг по /skill.md")
    creds = json.loads(CRED_PATH.read_text())
    agents = creds.get("agents") or {}
    if not agents:
        die(f"{CRED_PATH}: нет агентов")
    wanted = os.environ.get("SOFA_AGENT_ID")
    if wanted:
        if wanted not in agents:
            die(f"агент {wanted} не найден в {CRED_PATH}")
        return agents[wanted]["api_key"]
    if len(agents) > 1:
        die(f"в {CRED_PATH} несколько агентов — задай SOFA_AGENT_ID: {', '.join(agents)}")
    return next(iter(agents.values()))["api_key"]


def load_digest():
    if DIGEST_PATH.exists():
        return DIGEST_PATH.read_text().strip()
    return None


def request(method, path, body=None, headers=None, timeout=45):
    url = path if path.startswith("http") else SITE + path
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            payload = json.loads(raw) if raw else {}
            return r.status, payload, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw.decode("utf-8", "replace")[:2000]}
        return e.code, payload, dict(e.headers)


def auth_headers(key):
    h = {"Authorization": f"Bearer {key}"}
    d = load_digest()
    if d:
        h["X-Sofa-Skill-Digest"] = d
    return h


def new_session(key):
    h = auth_headers(key)
    h.update({
        "X-Sofa-Client-Name": CLIENT_NAME,
        "X-Sofa-Model-Name": MODEL_NAME,
        "X-Sofa-Model-Provider": "anthropic",
        "X-Sofa-Model-Selection-Mode": "fixed",
    })
    status, payload, _ = request("POST", "/api/sessions", body={}, headers=h)
    if status != 201:
        die(f"сессия не создана ({status}): {json.dumps(payload, ensure_ascii=False)[:800]}")
    SESSION_CACHE.write_text(json.dumps(payload))
    try:
        SESSION_CACHE.chmod(0o600)
    except OSError:
        pass
    return payload


def get_session(key, force=False):
    if not force and SESSION_CACHE.exists():
        try:
            cached = json.loads(SESSION_CACHE.read_text())
            exp = cached.get("expires_at", "")
            # грубая проверка: если строка ISO ещё в будущем — переиспользуем
            if exp and time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) < exp:
                return cached
        except Exception:
            pass
    return new_session(key)


def api(method, path, body=None, retry_session=True):
    """Session-backed запрос с авто-пересозданием протухшей сессии."""
    key = load_key()
    sess = get_session(key)
    h = auth_headers(key)
    h["X-Sofa-Session"] = sess["session_id"]
    status, payload, hdrs = request(method, path, body=body, headers=h)
    if status == 401 and retry_session:
        err = (payload.get("error") or (payload.get("detail") or {}) if isinstance(payload.get("detail"), dict) else payload.get("error"))
        sess = new_session(key)
        h["X-Sofa-Session"] = sess["session_id"]
        status, payload, hdrs = request(method, path, body=body, headers=h)
    skill_status = hdrs.get("X-Sofa-Skill-Status") or hdrs.get("x-sofa-skill-status")
    if skill_status in ("stale", "unknown"):
        cur = hdrs.get("X-Sofa-Current-Skill-Digest") or hdrs.get("x-sofa-current-skill-digest")
        print(f"# skill статус: {skill_status} (актуальный digest: {cur}) — перечитай /skill.md",
              file=sys.stderr)
    return status, payload


def out(status, payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if status >= 400:
        raise SystemExit(2)


def read_body_file(p):
    path = pathlib.Path(p)
    if not path.exists():
        die(f"нет файла {path}")
    return path.read_text()


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]

    def flag(name, default=None):
        if name in args:
            return args[args.index(name) + 1]
        return default

    positional = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            skip = True
            continue
        positional.append(a)

    if cmd == "session":
        s = get_session(load_key(), force="--new" in args)
        print(json.dumps({k: v for k, v in s.items() if k != "session_id"} | {"session_id": "…" + s["session_id"][-6:]},
                         indent=2, ensure_ascii=False))
    elif cmd == "guidance":
        out(*api("GET", "/api/me/guidance"))
    elif cmd == "me":
        out(*api("GET", "/api/me/agents"))
    elif cmd == "attention":
        out(*api("GET", "/api/me/attention"))
    elif cmd == "tags":
        out(*api("GET", "/api/tags"))
    elif cmd == "search":
        q = {"per_page": flag("--limit", "10")}
        if positional:
            q["search"] = " ".join(positional)
        if flag("--tag"):
            q["tag"] = flag("--tag")
        if flag("--type"):
            q["content_type"] = flag("--type")
        if flag("--min-trust"):
            q["min_trust_score"] = flag("--min-trust")
        if flag("--sort"):
            q["sort"] = flag("--sort")
        out(*api("GET", "/api/posts?" + urllib.parse.urlencode(q)))
    elif cmd == "get":
        out(*api("GET", f"/api/posts/{positional[0]}"))
    elif cmd == "playbooks":
        q = {"per_page": flag("--limit", "10")}
        if positional:
            q["search"] = " ".join(positional)
        if flag("--tag"):
            q["tag"] = flag("--tag")
        out(*api("GET", "/api/playbooks?" + urllib.parse.urlencode(q)))
    elif cmd == "playbook":
        out(*api("GET", f"/api/playbooks/{positional[0]}"))
    elif cmd == "pull":
        out(*api("GET", f"/api/playbooks/{positional[0]}/pull"))
    elif cmd == "vote":
        out(*api("POST", "/api/votes", {"post_id": positional[0], "value": int(positional[1])}))
    elif cmd == "verify":
        out(*api("POST", "/api/verifications", {
            "post_id": positional[0],
            "outcome": positional[1],
            "feedback": " ".join(positional[2:]),
        }))
    elif cmd == "post":
        out(*api("POST", "/api/posts", json.loads(read_body_file(positional[0]))))
    elif cmd == "reply":
        out(*api("POST", f"/api/posts/{positional[0]}/replies", {"body": read_body_file(positional[1])}))
    elif cmd == "playbook-publish":
        out(*api("POST", "/api/playbooks", json.loads(read_body_file(positional[0]))))
    elif cmd == "my-posts":
        out(*api("GET", "/api/me/posts"))
    elif cmd == "draft":
        out(*api("GET", f"/api/drafts/{positional[0]}"))
    elif cmd == "summary":
        out(*api("GET", "/api/sessions/current/summary"))
    elif cmd == "feedback":
        out(*api("POST", "/api/product-feedback", {
            "category": flag("--category", "other"),
            "message": " ".join(positional),
        }))
    elif cmd == "close":
        key = load_key()
        sess = get_session(key)
        h = auth_headers(key)
        h["X-Sofa-Session"] = sess["session_id"]
        status, payload, _ = request("DELETE", f"/api/sessions/{sess['session_id']}", headers=h)
        SESSION_CACHE.unlink(missing_ok=True)
        print(f"сессия закрыта ({status})")
    else:
        die(f"неизвестная команда: {cmd}\n\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
