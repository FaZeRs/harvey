# Mock-ups for interested prospects

When a prospect replies with interest and Harvey flagged their company's
website as outdated (`OUTDATED_SITE`), Harvey doesn't answer straight away.
It has OpenDesign build a modern
homepage concept from their current site, publishes it to a static previews
site, checks the page and its images load, and only then drafts the answer,
with the link. The answer waits in the Outbox like any other.

```
interested reply ─▶ job queued ─▶ OpenDesign run ─▶ files pushed to previews repo
                                                          │
                     Outbox ◀─ answer with link ◀─ page live (checked)
```

- One mock-up per company, at most `daily_limit` started a day.
- If the run fails, or the page isn't live after `timeout_minutes`, the
  answer still goes out, without a link. An interested prospect never waits
  on a design.
- SSH, git or network trouble is retried every heartbeat until the timeout.
- Each preview sits at `<base_url>/<company>-<random>/`, so links can't be
  guessed, and is marked `noindex`.

## Setup

### 1. A previews site

A Git repo whose pushes deploy as a static site (Vercel, Netlify, Cloudflare
Pages). Give it a `robots.txt` that disallows everything and a root page that
lists nothing. On Vercel, set `"trailingSlash": true` in `vercel.json` so
pages' relative image paths resolve. Clone it locally; Harvey commits and
pushes there, so the clone needs push access.

### 2. A restricted SSH key for OpenDesign

Harvey reads text from strangers (websites, replies), so the key it holds
should be able to do nothing but talk to OpenDesign. On the OpenDesign host,
install this as `/usr/local/bin/harvey-od`:

```sh
#!/bin/sh
# Forced command for Harvey's SSH key: the only things that key can do.
set -eu
set -- ${SSH_ORIGINAL_COMMAND:-}
case "${1:-}" in
  mcp)
    [ $# -eq 1 ] || exit 2
    exec docker exec -i open-design node /app/apps/daemon/bin/od.mjs mcp --daemon-url http://127.0.0.1:7456 ;;
  fetch)
    [ $# -eq 2 ] || exit 2
    printf '%s' "$2" | grep -Eq '^harvey-[a-z0-9-]{1,60}$' || { echo "fetch: bad project id" >&2; exit 2; }
    exec docker exec open-design tar -C "/app/.od/projects/$2" -cf - . ;;
  *)
    echo "harvey-od: not allowed" >&2; exit 2 ;;
esac
```

Adjust the container name and paths to your install. Then generate a key
(`ssh-keygen -t ed25519 -N "" -f ~/.ssh/harvey_opendesign`) and add it to the
host's `authorized_keys` with the restrictions:

```
restrict,command="/usr/local/bin/harvey-od" ssh-ed25519 AAAA... harvey-opendesign
```

`restrict` stops shells, port forwarding and agent forwarding. `fetch` only
reads projects Harvey created (`harvey-*`). Through `mcp` the key can still
reach OpenDesign's other tools; Harvey calls only `create_project`,
`start_run` and `get_run`.

### 3. Config

```yaml
mockups:
  enabled: true
  ssh_host: root@od.example.internal
  ssh_key: ~/.ssh/harvey_opendesign
  previews_repo: ~/previews
  base_url: https://preview.example.com
```

Make sure your previews domain is allowed in emails: a subdomain of your
sending domain or of an entry in `channels.email.link_domains` is.

## What OpenDesign is asked for

`harvey/mockups.py` → `brief()`: a mobile-first single page built only from
facts on the prospect's current site, their own photos, in their site's
language, with labelled placeholders where content is missing and a footer
saying it's a concept. It runs with the `redesign-existing-projects` skill.
The prospect's site is read by OpenDesign's own agent inside its container,
not by Harvey.
