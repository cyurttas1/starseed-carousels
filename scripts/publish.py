"""Starseed Instagram carousel publisher, run by GitHub Actions (stdlib only).

For every days/<date>/ folder (1.jpg-6.jpg + post.json) whose 19:00 TR slot is near or past and that has no
state/<date>.json with "ig_media" yet:
    1. child containers from the raw.githubusercontent.com copies of the slides, then the carousel container
    2. wait until the slot (never earlier), publish
    3. 3 minutes later the first comment (book link) on the matching Facebook post
Every step is saved to state/<date>.json first, so a re-run never posts twice. A slot more than LATE_HOURS past is marked missed.

    python scripts/publish.py [--dry-run] [--date YYYY-MM-DD]
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
        sleep_until(slot + dt.timedelta(minutes=3))
        r = post(f"{p['fb_post']}/comments", message=f"{p['first_comment']}\n{p['book_url']}")
        state_of(date, fb_comment=r.get("id")); log(f"{date}: first comment on Facebook")


def main():
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
