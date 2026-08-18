#!/usr/bin/env python3
"""Henter opslag + tal fra LinkedIn, Facebook og Instagram og skriver data/posts.json.

    python3 fetch_some_data.py                  # henter alt der er konfigureret
    python3 fetch_some_data.py --only instagram
    python3 fetch_some_data.py --discover       # finder Page-/IG-/org-id'er ud fra dine tokens

Verificeret mod dokumentationen august 2026:
  · Graph API v26.0 (v25 udfasede post_impressions* — se REACH_FIELD nedenfor)
  · LinkedIn versionsheader 202608, Posts API + organizationalEntityShareStatistics

Scriptet er additivt: hver kørsel lægger et dagligt snapshot oveni historikken i
posts.json, så dashboardet kan vise udvikling. Historik kan ikke hentes bagud.

Miljøvariabler (se OPSAETNING.md):
    META_TOKEN   system user-token (udløber ikke)   FB_PAGE_ID   FB_PAGE_NAME
    IG_USER_ID   IG_USER_NAME
    LI_TOKEN     LI_ORG_ID   LI_ORG_NAME
Manglende variabler springes over — de øvrige platforme hentes alligevel.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GRAPH = "https://graph.facebook.com/v26.0"
LI_API = "https://api.linkedin.com/rest"
LI_VERSION = "202608"        # PÅKRÆVET header. Hver version lever ~1 år — bump årligt.
OUT = Path("data/posts.json")
TIMEOUT = 45

# Metas *_unique-metrics blev udfaset over v25. Erstatninger ifølge v25-changeloggen:
#   post_impressions_unique  ->  post_total_media_view_unique   (reach)
#   post_impressions         ->  post_media_view                (visninger)
FB_REACH = "post_total_media_view_unique"
FB_VIEWS = "post_media_view"
FB_BASE_METRICS = [FB_VIEWS, FB_REACH, "post_clicks", "post_reactions_by_type_total"]
FB_VIDEO_METRICS = ["post_video_views", "post_video_avg_time_watched", "post_video_length"]
# ældre navne beholdes som fallback, hvis kontoen stadig får dem serveret
FB_LEGACY = {"post_impressions": FB_VIEWS, "post_impressions_unique": FB_REACH}

# Instagram: metrics er medietype-afhængige. Blandes forkerte sammen, fejler HELE kaldet.
IG_COMMON = ["reach", "views", "total_interactions", "shares"]
IG_FEED_ONLY = ["saved", "likes", "comments", "profile_visits", "follows"]
IG_REELS_ONLY = ["saved", "likes", "comments", "ig_reels_avg_watch_time",
                 "ig_reels_video_view_total_time"]


# ---------------------------------------------------------------- transport


def get_json(url: str, headers: dict | None = None, retries: int = 2) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            # 429/rate limit: vent og prøv igen. Ellers op med det samme.
            if e.code in (429, 613) and attempt < retries:
                time.sleep(8 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {body}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(3)
                continue
            raise RuntimeError(f"netværksfejl: {e.reason}") from None
    raise RuntimeError("uventet")


def insights_map(node: dict) -> dict:
    """Pak {"insights": {"data":[{"name":..,"values":[{"value":..}]}]}} ud til et fladt dict.

    Meta returnerer et TOMT datasæt i stedet for 0, når en metric ikke findes.
    """
    out = {}
    for row in ((node.get("insights") or {}).get("data") or node.get("data") or []):
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value")
    return out


def thumb_b64(url: str, box: int = 400) -> str | None:
    """Hent medie og komprimér til base64 — alle tre platformes medie-URL'er er
    signerede og udløber, så de kan ikke bare gemmes som links."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            raw = r.read()
    except Exception:
        return None
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((box, box))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        if len(raw) < 400_000:
            return "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        return None


def aspect_of(w, h) -> str:
    if not w or not h:
        return "square"
    r = w / h
    return "portrait" if r < 0.92 else "landscape" if r > 1.08 else "square"


def blank(**kw) -> dict:
    m = dict(impressions=None, reach=None, reactions=None, comments=None, shares=None,
             saves=None, engagements=None, clicks=None, follows=None, video_views=None,
             video_length_s=None, avg_watch_time_s=None, completion_rate=None)
    m.update(kw)
    return m


def ms_to_s(v):
    if not isinstance(v, (int, float)):
        return None
    return round(v / 1000, 1) if v > 500 else round(float(v), 1)


# ---------------------------------------------------------------- Facebook


def page_token(page_id: str, user_token: str) -> str:
    """Veksl bruger-/systembruger-token til et SIDE-token.

    Uden dette svarer Graph API:
      (#210) A page access token is required to request this resource.
    Sidens opslag og insights kan kun læses med sidens eget token.
    """
    try:
        d = get_json(f"{GRAPH}/{page_id}?fields=access_token&access_token={user_token}")
        t = d.get("access_token")
        if t:
            return t
        print("  · intet side-token i svaret — bruger det oprindelige", file=sys.stderr)
    except RuntimeError as e:
        print(f"  · kunne ikke veksle til side-token: {e}", file=sys.stderr)
    return user_token


def fetch_facebook(page_id: str, token: str, since: datetime) -> list[dict]:
    """/{page-id}/published_posts — Pagens egne opslag. /posts er udokumenteret,
    /feed blander besøgendes indhold ind og kræver ekstra permission."""
    token = page_token(page_id, token)
    fields = (
        "id,created_time,message,permalink_url,full_picture,is_published,"
        "attachments{media_type,type,media},shares,"
        "comments.summary(true).limit(0),likes.summary(true).limit(0),"
        f"insights.metric({','.join(FB_BASE_METRICS)})"
    )
    url = (f"{GRAPH}/{page_id}/published_posts?fields={urllib.parse.quote(fields)}"
           f"&since={int(since.timestamp())}&limit=50&access_token={token}")

    raw = []
    while url:
        d = get_json(url)
        raw += d.get("data", [])
        url = d.get("paging", {}).get("next")
        if len(raw) > 400:
            break

    out = []
    for p in raw:
        ins = insights_map(p)
        for old, new in FB_LEGACY.items():          # tolerér begge navnesæt
            if ins.get(new) is None and ins.get(old) is not None:
                ins[new] = ins[old]

        att = ((p.get("attachments") or {}).get("data") or [{}])[0]
        kind = att.get("media_type") or att.get("type") or ""
        mtype = {"video": "video", "video_inline": "video", "video_autoplay": "video",
                 "photo": "image", "album": "carousel", "share": "image",
                 "link": "image"}.get(kind, "text")

        # videometrics hentes kun for videoopslag — ellers fejler hele kaldet
        if mtype == "video":
            try:
                v = get_json(f"{GRAPH}/{p['id']}/insights"
                             f"?metric={','.join(FB_VIDEO_METRICS)}&access_token={token}")
                ins.update(insights_map(v))
            except RuntimeError as e:
                print(f"  · videometrics for {p['id']}: {e}", file=sys.stderr)

        reac = ins.get("post_reactions_by_type_total")
        reactions = sum(reac.values()) if isinstance(reac, dict) else (reac or None)
        if reactions is None:
            reactions = (p.get("likes", {}).get("summary", {}) or {}).get("total_count")
        comments = (p.get("comments", {}).get("summary", {}) or {}).get("total_count")
        shares = (p.get("shares") or {}).get("count", 0)

        img = (att.get("media") or {}).get("image") or {}
        length = ms_to_s(ins.get("post_video_length"))
        avg = ms_to_s(ins.get("post_video_avg_time_watched"))

        out.append({
            "id": p["id"],
            "platform": "facebook",
            "account": os.environ.get("FB_PAGE_NAME", "Facebook Page"),
            "published_at": p["created_time"],
            "permalink": p.get("permalink_url"),
            "caption": (p.get("message") or "").strip(),
            "media_type": mtype,
            "media_aspect": aspect_of(img.get("width"), img.get("height")),
            "thumbnail": thumb_b64(p.get("full_picture")) if mtype != "text" else None,
            "metrics": blank(
                impressions=ins.get(FB_VIEWS),
                reach=ins.get(FB_REACH),
                reactions=reactions, comments=comments, shares=shares,
                engagements=sum(v for v in (reactions, comments, shares) if isinstance(v, int)) or None,
                clicks=ins.get("post_clicks"),
                video_views=ins.get("post_video_views"),
                video_length_s=length, avg_watch_time_s=avg,
                completion_rate=round(avg / length, 4) if avg and length else None,
            ),
        })
    return out


# ---------------------------------------------------------------- Instagram


def fetch_instagram(ig_id: str, token: str, since: datetime) -> list[dict]:
    """Instagram API with Facebook Login (graph.facebook.com). Den vej valgt frem for
    Instagram Login, fordi 1) system user-tokenet udløber ikke, og 2) feltet
    media_product_type — der adskiller reels fra feed — kun findes på denne vej."""
    fields = ("id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,"
              "timestamp,like_count,comments_count,username")
    url = (f"{GRAPH}/{ig_id}/media?fields={urllib.parse.quote(fields)}"
           f"&since={int(since.timestamp())}&limit=50&access_token={token}")

    media = []
    while url:
        d = get_json(url)
        media += d.get("data", [])
        url = d.get("paging", {}).get("next")
        if len(media) > 400:
            break

    out = []
    for m in media:
        ts = datetime.fromisoformat(m["timestamp"].replace("+0000", "+00:00"))
        if ts < since:
            continue
        is_reel = (m.get("media_product_type") == "REELS") or m.get("media_type") == "VIDEO"

        # profile_visits og follows findes IKKE på reels. Ét ugyldigt metric-navn
        # fejler hele kaldet, så de to grupper holdes adskilt.
        wanted = IG_COMMON + (IG_REELS_ONLY if is_reel else IG_FEED_ONLY)
        ins = {}
        try:
            ins = insights_map(get_json(f"{GRAPH}/{m['id']}/insights"
                                        f"?metric={','.join(wanted)}&access_token={token}"))
        except RuntimeError:
            for name in wanted:                     # fald tilbage til én ad gangen
                try:
                    ins.update(insights_map(get_json(
                        f"{GRAPH}/{m['id']}/insights?metric={name}&access_token={token}")))
                except RuntimeError:
                    continue

        likes = ins.get("likes")
        if likes is None:
            likes = m.get("like_count")
        comments = ins.get("comments")
        if comments is None:
            comments = m.get("comments_count")
        saves, shares = ins.get("saved"), ins.get("shares")
        eng = ins.get("total_interactions")
        if eng is None:
            eng = sum(v for v in (likes, comments, saves, shares) if isinstance(v, int)) or None

        mtype = {"IMAGE": "image", "VIDEO": "video", "CAROUSEL_ALBUM": "carousel"}.get(
            m.get("media_type"), "image")
        out.append({
            "id": m["id"],
            "platform": "instagram",
            "account": "@" + (m.get("username") or os.environ.get("IG_USER_NAME", "instagram")),
            "published_at": m["timestamp"],
            "permalink": m.get("permalink"),
            "caption": (m.get("caption") or "").strip(),
            "media_type": "video" if is_reel else mtype,
            "media_aspect": "portrait" if is_reel else "square",
            "thumbnail": thumb_b64(m.get("thumbnail_url") or m.get("media_url")),
            "metrics": blank(
                impressions=ins.get("views"),
                reach=ins.get("reach"),
                reactions=likes, comments=comments, shares=shares, saves=saves,
                engagements=eng,
                follows=ins.get("follows"),
                video_views=ins.get("views") if is_reel else None,
                avg_watch_time_s=ms_to_s(ins.get("ig_reels_avg_watch_time")),
            ),
        })
    return out


# ---------------------------------------------------------------- LinkedIn


def li_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "LinkedIn-Version": LI_VERSION,
            "X-Restli-Protocol-Version": "2.0.0"}


def fetch_linkedin(org_id: str, token: str, since: datetime) -> list[dict]:
    H = li_headers(token)
    org_urn = f"urn:li:organization:{org_id}"

    # 1) opslag via Posts API. ugcPosts og shares er legacy.
    posts, start = [], 0
    while start <= 300:
        d = get_json(f"{LI_API}/posts?author={urllib.parse.quote(org_urn)}&q=author"
                     f"&count=50&start={start}&sortBy=LAST_MODIFIED", H)
        batch = d.get("elements", [])
        posts += batch
        # Dokumentationen advarer: en kort side betyder IKKE at der ikke er flere.
        if not batch:
            break
        start += 50

    def when(p):
        return (p.get("publishedAt") or p.get("createdAt") or 0) / 1000

    posts = [p for p in posts
             if datetime.fromtimestamp(when(p), timezone.utc) >= since]
    posts = {p["id"]: p for p in posts}.values()      # Posts API kan gentage sig
    if not posts:
        return []

    # 2) statistik. /rest/posts returnerer BÅDE urn:li:share og urn:li:ugcPost,
    #    og de skal sendes i hver deres parameter — ellers droppes de i stilhed.
    stats: dict[str, dict] = {}
    groups = {"shares": [p["id"] for p in posts if ":share:" in p["id"]],
              "ugcPosts": [p["id"] for p in posts if ":ugcPost:" in p["id"]]}
    for key, urns in groups.items():
        for i in range(0, len(urns), 20):
            chunk = urns[i:i + 20]
            params = "&".join(f"{key}[{j}]={urllib.parse.quote(u)}"
                              for j, u in enumerate(chunk))
            try:
                d = get_json(f"{LI_API}/organizationalEntityShareStatistics"
                             f"?q=organizationalEntity"
                             f"&organizationalEntity={urllib.parse.quote(org_urn)}&{params}", H)
            except RuntimeError as e:
                print(f"  · LinkedIn-statistik ({key}): {e}", file=sys.stderr)
                continue
            for el in d.get("elements", []):
                urn = el.get("share") or el.get("ugcPost")
                if urn:
                    stats[urn] = el.get("totalShareStatistics", {})

    out = []
    for p in posts:
        s = stats.get(p["id"], {})
        content = p.get("content") or {}
        media = content.get("media") or {}
        mid = media.get("id") or (content.get("article") or {}).get("thumbnail")

        if "multiImage" in content:
            mtype = "carousel"
        elif mid and str(mid).startswith("urn:li:video"):
            mtype = "video"
        elif mid:
            mtype = "image"
        else:
            mtype = "text"

        thumb, aspect = None, "square"
        if mid:
            # Enkelt-GET, ikke ?ids=List(...): BATCH_GET er helt spærret i Development Tier.
            kind = "videos" if str(mid).startswith("urn:li:video") else "images"
            try:
                mm = get_json(f"{LI_API}/{kind}/{urllib.parse.quote(str(mid))}", H)
                thumb = thumb_b64(mm.get("thumbnail") or mm.get("downloadUrl"))
                aspect = aspect_of(mm.get("aspectRatioWidth"), mm.get("aspectRatioHeight"))
            except RuntimeError as e:
                print(f"  · LinkedIn-medie {mid}: {e}", file=sys.stderr)

        reactions, comments = s.get("likeCount"), s.get("commentCount")
        shares = s.get("shareCount")
        # tidsafgrænsede svar staver feltet i flertal — tag imod begge
        reach = s.get("uniqueImpressionsCount") or s.get("uniqueImpressionsCounts")
        out.append({
            "id": p["id"],
            "platform": "linkedin",
            "account": os.environ.get("LI_ORG_NAME", "LinkedIn Page"),
            "published_at": datetime.fromtimestamp(when(p), timezone.utc).isoformat(),
            "permalink": f"https://www.linkedin.com/feed/update/{p['id']}/",
            "caption": (p.get("commentary") or "").strip(),
            "media_type": mtype,
            "media_aspect": aspect,
            "thumbnail": thumb,
            "metrics": blank(
                impressions=s.get("impressionCount"),
                reach=reach,
                reactions=reactions, comments=comments, shares=shares,
                engagements=sum(v for v in (reactions, comments, shares)
                                if isinstance(v, int)) or None,
                clicks=s.get("clickCount"),
            ),
        })
    return out


# ---------------------------------------------------------------- discover


def discover() -> None:
    """Print de id'er du skal lægge i miljøvariablerne."""
    t = os.environ.get("META_TOKEN")
    if t:
        try:
            for pg in get_json(f"{GRAPH}/me/accounts?fields=id,name&access_token={t}").get("data", []):
                print(f"FB_PAGE_ID={pg['id']}   # {pg['name']}")
                ig = get_json(f"{GRAPH}/{pg['id']}"
                              f"?fields=instagram_business_account{{id,username}}&access_token={t}")
                acc = ig.get("instagram_business_account")
                if acc:
                    print(f"IG_USER_ID={acc['id']}   # @{acc.get('username','?')}")
        except RuntimeError as e:
            print(f"Meta: {e}", file=sys.stderr)
    else:
        print("META_TOKEN ikke sat — springer Meta over", file=sys.stderr)

    lt = os.environ.get("LI_TOKEN")
    if lt:
        try:
            d = get_json(f"{LI_API}/organizationAcls?q=roleAssignee"
                         f"&role=ADMINISTRATOR&state=APPROVED", li_headers(lt))
            for el in d.get("elements", []):
                urn = el.get("organization", "")
                print(f"LI_ORG_ID={urn.rsplit(':', 1)[-1]}   # {urn}")
        except RuntimeError as e:
            print(f"LinkedIn: {e}", file=sys.stderr)
    else:
        print("LI_TOKEN ikke sat — springer LinkedIn over", file=sys.stderr)


# ---------------------------------------------------------------- historik


def merge_history(new_posts: list[dict], old_doc: dict) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    old = {p["id"]: p for p in old_doc.get("posts", [])}
    for p in new_posts:
        prev = old.get(p["id"]) or {}
        hist = list(prev.get("history") or [])
        m = p["metrics"]
        snap = {"d": today, "reach": m.get("reach") or 0,
                "impressions": m.get("impressions") or 0,
                "engagements": m.get("engagements") or 0}
        if hist and hist[-1]["d"] == today:
            hist[-1] = snap
        else:
            hist.append(snap)
        p["history"] = hist[-90:]
        if not p.get("thumbnail") and prev.get("thumbnail"):
            p["thumbnail"] = prev["thumbnail"]      # behold billedet hvis mediet fejlede


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--only", choices=["facebook", "instagram", "linkedin"], action="append")
    ap.add_argument("--discover", action="store_true",
                    help="find Page-/IG-/org-id'er ud fra dine tokens og afslut")
    a = ap.parse_args()

    if a.discover:
        discover()
        return 0

    since = datetime.now(timezone.utc) - timedelta(days=a.days)
    want = set(a.only or ["facebook", "instagram", "linkedin"])
    meta_token = os.environ.get("META_TOKEN")
    posts, accounts, problems = [], [], []

    jobs = [
        ("facebook", lambda: fetch_facebook(os.environ["FB_PAGE_ID"], meta_token, since),
         bool(meta_token and os.environ.get("FB_PAGE_ID"))),
        ("instagram", lambda: fetch_instagram(os.environ["IG_USER_ID"], meta_token, since),
         bool(meta_token and os.environ.get("IG_USER_ID"))),
        ("linkedin", lambda: fetch_linkedin(os.environ["LI_ORG_ID"], os.environ["LI_TOKEN"], since),
         bool(os.environ.get("LI_TOKEN") and os.environ.get("LI_ORG_ID"))),
    ]

    for name, fn, ready in jobs:
        if name not in want:
            continue
        if not ready:
            problems.append(f"{name}: mangler miljøvariabler — sprunget over")
            print(f"– {name}: ikke konfigureret", file=sys.stderr)
            continue
        try:
            got = fn()
            posts += got
            if got:
                accounts.append({"platform": name, "name": got[0]["account"], "id": name})
            print(f"✓ {name}: {len(got)} opslag")
        except Exception as e:
            problems.append(f"{name}: {e}")
            print(f"✗ {name}: {e}", file=sys.stderr)

    if not posts:
        print("Ingen opslag hentet — posts.json er urørt.", file=sys.stderr)
        return 1

    old_doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if old_doc.get("source") == "demo":
        old_doc = {}                     # bland ikke demo-historik ind i rigtige tal
    merge_history(posts, old_doc)

    # Bevar opslag der er faldet ud af hentevinduet, så arkivet vokser i stedet
    # for at skrumpe. De opdateres ikke længere, men deres historik bevares.
    hentede = {p["id"] for p in posts}
    for gammelt in old_doc.get("posts", []):
        if gammelt.get("id") not in hentede:
            posts.append(gammelt)

    posts.sort(key=lambda p: p["published_at"], reverse=True)

    doc = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "live",
        "accounts": accounts,
        "problems": problems,
        "posts": posts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"Skrev {OUT}: {len(posts)} opslag, {OUT.stat().st_size/1024:.0f} KB")
    if problems:
        print("Bemærk:\n  " + "\n  ".join(problems), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())