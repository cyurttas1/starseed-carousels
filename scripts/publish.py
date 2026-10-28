"""Starseed Instagram carousel publisher, run by GitHub Actions (stdlib only).

For every days/<date>/ folder (1.jpg-6.jpg + post.json) whose 19:00 TR slot is near or past and that has no
state/<date>.json with "ig_media" yet:
    1. child containers from the raw.githubusercontent.com copies of the slides, then the carousel container
    2. wait until the slot (never earlier), publish
    3. 3 minutes later the first comment (book link) on the matching Facebook post
Every step is saved to state/<date>.json first, so a re-run never posts twice. A slot more than LATE_HOURS past is marked missed.

    python scripts/publish.py [--dry-run] [--date YYYY-MM-DD]
    python scripts/publish.py --comments    first comments under every Facebook video (videos/<date>.json; book link, Mon/Wed/Fri also
                                            the affiliate invite), store promo post (promos/promos.json) and carousel post (days/<date>/post.json).
                                            Facebook accepts a comment on a still-scheduled post, so the comment is attached as soon as the
                                            post exists (push trigger) and is only re-checked after the slot; GitHub's own cron runs hours late.
Env: FB_PAGE_ID, FB_PAGE_TOKEN, GITHUB_REPOSITORY (owner/name), GITHUB_REF_NAME
"""
import sys, os, json, time, pathlib, datetime as dt, urllib.request, urllib.parse, urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent
GRAPH = "https://graph.facebook.com/v25.0"
TR = dt.timezone(dt.timedelta(hours=3))
SLOT = dt.time(19, 0)
EARLY = dt.timedelta(minutes=25)  # container work may start this long before the slot
LATE_HOURS = 20


def now(): return dt.datetime.now(TR)
def log(m): print(f"{now():%m-%d %H:%M:%S} {m}", flush=True)


def call(method, url, fields=None):
    data = urllib.parse.urlencode(fields).encode() if fields else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {e.code}: {e.read().decode(errors='replace')[:400]}") from None


def graph(path, **q):
    q["access_token"] = os.environ["FB_PAGE_TOKEN"]
    return call("GET", f"{GRAPH}/{path}?{urllib.parse.urlencode(q)}")


def post(path, **f):
    f["access_token"] = os.environ["FB_PAGE_TOKEN"]
    return call("POST", f"{GRAPH}/{path}", f)


def state_of(date, **kw):
    f = ROOT / "state" / f"{date}.json"; f.parent.mkdir(exist_ok=True)
    s =json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if kw:
        s.update(kw); f.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


def wait_container(cid, tries=45):
    for _ in range(tries):
        r = graph(cid, fields="status_code,status")
        if r.get("status_code") == "FINISHED": return
        if r.get("status_code") in ("ERROR", "EXPIRED"): raise RuntimeError(f"container {cid} {r.get('status_code')}: {r.get('status')}")
        time.sleep(4)
    raise RuntimeError(f"container {cid} not ready after 3 minutes")


def sleep_until(t):
    while (left := (t - now()).total_seconds()) > 0:
        time.sleep(min(left, 30))


def run_day(date, dry):
    day = ROOT / "days" / date
    p = json.loads((day / "post.json").read_text(encoding="utf-8"))
    slot = dt.datetime.combine(dt.date.fromisoformat(date), SLOT, tzinfo=TR)
    s = state_of(date)
    if s.get("ig_media"):
        pass
    elif now() < slot - EARLY and not dry:
        log(f"{date}: not due yet"); return
    elif now() > slot + dt.timedelta(hours=LATE_HOURS):
        if not s.get("missed"): state_of(date, missed=now().isoformat(timespec="minutes"))
        log(f"{date}: slot missed by more than {LATE_HOURS} h, not posting"); return
    else:
        ig = s.get("ig_id") or graph(os.environ["FB_PAGE_ID"], fields="instagram_business_account")["instagram_business_account"]["id"]
        state_of(date, ig_id=ig)
        repo, ref = os.environ["GITHUB_REPOSITORY"], os.environ.get("GITHUB_REF_NAME", "main")
        if not s.get("ig_container"):
            kids = []
            for i in range(1, 7):
                url = f"https://raw.githubusercontent.com/{repo}/{ref}/days/{date}/{i}.jpg"
                c = post(f"{ig}/media", image_url=url, is_carousel_item="true")["id"]; wait_container(c); kids.append(c)
            c = post(f"{ig}/media", media_type="CAROUSEL", children=",".join(kids), caption=p["ig_caption"])["id"]
            wait_container(c)
            if dry: log(f"{date}: dry run, container {c} ready, not saved, not published"); return  # containers expire after 24 h
            state_of(date, ig_container=c); log(f"{date}: container {c} ready")
        if dry: log(f"{date}: dry run, stopping before publish"); return
        sleep_until(slot)
        m = post(f"{ig}/media_publish", creation_id=state_of(date)["ig_container"])["id"]
        link = graph(m, fields="permalink").get("permalink")
        state_of(date, ig_media=m, ig_link=link, ig_at=now().isoformat(timespec="minutes")); log(f"{date}: published {link}")
    s = state_of(date)
    if s.get("ig_media") and not s.get("fb_comment") and p.get("fb_post") and not dry:
        cid, _ = ensure_comment(p["fb_post"], f"{p['first_comment']}\n{p['book_url']}")
        state_of(date, fb_comment=cid); log(f"{date}: first comment on Facebook")


AFFILIATE_COMMENT = ("✦ Do you create content about spirituality, healing or self-growth? Share our books with your audience "
                     "and earn 25% on every sale. Become a Starseed Transmission affiliate: https://starseedtransmission.gumroad.com/affiliates")
AFFILIATE_DAYS = {0, 2, 4}  # Mon/Wed/Fri by slot date
CHECK_FROM = dt.timedelta(minutes=3)  # after the slot the comment is verified (and posted if it did not survive publishing)
CHECK_DAYS = 10


def norm(t): return " ".join((t or "").split())


def find_comment(obj, text):
    """Id of our comment (same text) already under `obj`, else None."""
    url = f"{GRAPH}/{obj}/comments?" + urllib.parse.urlencode(
        {"fields": "message", "limit": 100, "order": "chronological", "access_token": os.environ["FB_PAGE_TOKEN"]})
    for _ in range(10):
        r = call("GET", url)
        for c in r.get("data", []):
            if norm(c.get("message")) == norm(text): return c["id"]
        url = r.get("paging", {}).get("next")
        if not url: return None
    return None


def ensure_comment(obj, text):
    """(comment id, created now). Never posts the same text twice under one post."""
    cid = find_comment(obj, text)
    if cid: return cid, False
    return post(f"{obj}/comments", message=text)["id"], True


def sync_comments(label, obj, slot, wanted, s, save):
    """wanted = [(state key, text)]. Before the slot: attach. After slot + CHECK_FROM: verify once, post what is missing."""
    if s.get("verified"): return
    if now() > slot + dt.timedelta(days=CHECK_DAYS): log(f"{label}: older than {CHECK_DAYS} days, skipped"); return
    for key, text in wanted:
        cid, created = ensure_comment(obj, text)
        if created or s.get(key) != cid:
            save(**{key: cid}); log(f"{label}: {'posted' if created else 'found'} {key}")
    if now() >= slot + CHECK_FROM: save(verified=now().isoformat(timespec="minutes")); log(f"{label}: verified after the slot")


def json_state(f):
    f.parent.mkdir(exist_ok=True)
    s = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    def save(**kw): s.update(kw); f.write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s, save


def run_video(date):
    v = json.loads((ROOT / "videos" / f"{date}.json").read_text(encoding="utf-8"))
    slot = dt.datetime.fromisoformat(v["slot"]); s, save = json_state(ROOT / "state" / f"video-{date}.json")
    wanted = [("fb_comment", v["first_comment"])]
    if slot.weekday() in AFFILIATE_DAYS: wanted.append(("fb_comment2", AFFILIATE_COMMENT))
    sync_comments(f"video {date}", v["video_id"], slot, wanted, s, save)


def run_promo(p, state, save_all):
    slot = dt.datetime.fromisoformat(p["when"]); k = p["fb_id"]
    s = {"fb_comment": state.get(k)} if state.get(k) else {}
    if k in state.get("_verified", []): return
    def save(**kw):
        if "fb_comment" in kw: state[k] = kw["fb_comment"]
        if "verified" in kw: state.setdefault("_verified", []).append(k)
        save_all()
    sync_comments(f"promo {p['when'][:13]}", k, slot, [("fb_comment", p["comment"])], s, save)


def run_day_comment(date):
    day = ROOT / "days" / date; p = json.loads((day / "post.json").read_text(encoding="utf-8"))
    if not p.get("fb_post"): return
    slot = dt.datetime.combine(dt.date.fromisoformat(date), SLOT, tzinfo=TR); s, save = json_state(ROOT / "state" / f"{date}.json")
    if s.get("fb_comment_verified") or now() > slot + dt.timedelta(days=CHECK_DAYS): return
    cid, created = ensure_comment(p["fb_post"], f"{p['first_comment']}\n{p['book_url']}")
    if created or s.get("fb_comment") != cid: save(fb_comment=cid); log(f"carousel {date}: {'posted' if created else 'found'} first comment")
    if now() >= slot + CHECK_FROM: save(fb_comment_verified=now().isoformat(timespec="minutes"))


def main_comments():
    """Videos, store promos and carousel posts. One failure never blocks the others; exit 1 at the end so the run shows red."""
    failed = False
    def guard(label, fn, *a):
        nonlocal failed
        try: fn(*a)
        except Exception as ex:  # noqa: BLE001
            failed = True; log(f"{label}: ERROR {ex}")
    for f in sorted((ROOT / "videos").glob("*.json")): guard(f"video {f.stem}", run_video, f.stem)
    pf = ROOT / "state" / "promos.json"; pf.parent.mkdir(exist_ok=True)
    state = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
    for p in json.loads((ROOT / "promos" / "promos.json").read_text(encoding="utf-8")):
        guard(f"promo {p['when'][:13]}", run_promo, p, state, lambda: pf.write_text(json.dumps(state, indent=1), encoding="utf-8"))
    for d in sorted((ROOT / "days").iterdir()):
        if (d / "post.json").exists(): guard(f"carousel {d.name}", run_day_comment, d.name)
    sys.exit(1 if failed else 0)


def main():
    if "--comments" in sys.argv: return main_comments()
    a = sys.argv[1:]; dry = "--dry-run" in a
    dates = [a[a.index("--date") + 1]] if "--date" in a else sorted(d.name for d in (ROOT / "days").iterdir() if (d / "post.json").exists())
    failed = False
    for date in dates:
        try: run_day(date, dry)
        except Exception as ex:  # noqa: BLE001
            failed = True; log(f"{date}: ERROR {ex}"); state_of(date, last_error=str(ex)[-300:], error_at=now().isoformat(timespec="minutes"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
