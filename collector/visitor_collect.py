"""
collector/visitor_collect.py

Headed Playwright visitor-review collector, parameterised by place_id.
Ported faithfully from collect_reviews_expand_step2.py (visitor path only).
Blog path omitted; no module-level PLACE_ID/HOME_URL globals.
"""
import asyncio
import json
import random
import time

from config import PROXY_URL
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

REF_TOTALS = {"blog": 281, "visitor": 1159}
INCREMENTAL_OVERLAP_DAYS = 30

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_SCROLL_JS = """() => {
    const candidates = [
        ...document.querySelectorAll('[class*="list"]'),
        ...document.querySelectorAll('[class*="scroll"]'),
        ...document.querySelectorAll('[class*="review"]'),
        ...document.querySelectorAll('[class*="content"]'),
    ].filter(el => el.scrollHeight > el.clientHeight + 50);
    for (const el of candidates) { el.scrollTop = el.scrollHeight; }
    window.scrollTo(0, document.body.scrollHeight);
    document.documentElement.scrollTop = document.documentElement.scrollHeight;
    return {done: true, bodyH: document.body.scrollHeight};
}"""


# ─────────────────────────── helpers ───────────────────────────

def deep_find(obj, key, depth=0):
    if depth > 12:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = deep_find(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = deep_find(item, key, depth + 1)
            if r is not None:
                return r
    return None


def _should_stop_incremental(oldest_visit_date: str | None, since_date: str | None) -> bool:
    """True when oldest_visit_date is strictly older than since_date."""
    if not since_date or not oldest_visit_date:
        return False
    return oldest_visit_date[:10] < since_date[:10]


async def human_delay(min_s=1.5, max_s=3.0):
    await asyncio.sleep(random.uniform(min_s, max_s))


async def scroll_bottom(page):
    try:
        await page.evaluate(_SCROLL_JS)
        await page.mouse.wheel(0, 5000)
    except Exception:
        pass
    await asyncio.sleep(0.8)


async def find_expand_btn(page):
    """
    Find "펼쳐서 더보기" in main page and all sub-frames.
    Returns Locator or None.
    """
    loc = page.get_by_text("펼쳐서 더보기", exact=True)
    try:
        if await loc.count() > 0:
            return loc.last
    except Exception:
        pass
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        try:
            fl = frame.get_by_text("펼쳐서 더보기", exact=True)
            if await fl.count() > 0:
                return fl.last
        except Exception:
            pass
    return None


async def click_sort_latest(page, log_fn):
    """Click 최신순; return (success, raw_outerHTML).
    Per-attempt timeout capped at 6 s so 3 failures cost ≤ ~21 s total."""
    for attempt in range(3):
        try:
            btn = page.get_by_text("최신순", exact=True).first
            raw_html = await btn.evaluate("el => el.outerHTML", timeout=6_000)
            log_fn(f"[SORT] 최신순 outerHTML: {raw_html}")
            await btn.click()
            await asyncio.sleep(2.5)
            return True, raw_html
        except Exception as e:
            log_fn(f"[SORT] attempt {attempt+1} 실패: {e}")
            await asyncio.sleep(1.0)
    return False, None


def extract_batches(all_captures, start_idx, op_name, gql_key):
    """Return list of vr dicts with items from all_captures[start_idx:]."""
    results = []
    for c in all_captures[start_idx:]:
        if not c.get("is_gql"):
            continue
        if op_name not in str(c.get("post_data") or ""):
            continue
        bj = c.get("body_json")
        if bj is None:
            continue
        vr = deep_find(bj, gql_key)
        if isinstance(vr, dict) and isinstance(vr.get("items"), list):
            results.append(vr)
    return results


async def wait_new_gql(all_captures, pre_count, op_name, gql_key, timeout_s=10.0):
    """
    Poll until a new GQL response matching op_name appears after pre_count.
    Returns True when response arrives (even empty items = server responded).
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for c in all_captures[pre_count:]:
            if not c.get("is_gql"):
                continue
            if op_name not in str(c.get("post_data") or ""):
                continue
            bj = c.get("body_json")
            if bj is None:
                continue
            vr = deep_find(bj, gql_key)
            if isinstance(vr, dict):
                return True
        await asyncio.sleep(0.4)
    return False


def log_gql_ops(all_captures, start_idx, log_fn):
    """Log all distinct GQL operationNames seen from start_idx onward."""
    ops = set()
    for c in all_captures[start_idx:]:
        if not c.get("is_gql"):
            continue
        pd = c.get("post_data") or ""
        try:
            parsed = json.loads(pd)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        op = item.get("operationName")
                        if op:
                            ops.add(op)
            elif isinstance(parsed, dict):
                op = parsed.get("operationName")
                if op:
                    ops.add(op)
        except Exception:
            pass
    if ops:
        log_fn(f"[GQL OPS] detected: {sorted(ops)}")


# ─────────────────────────── main tab processor ───────────────────────────

async def process_tab(page, tab_name, all_captures, log_fn, place_id, since_date=None):
    """
    Full expand loop for one review tab.
    Returns (items_deduped, meta_dict).
    place_id drives HOME_URL and fallback_url; no module-global PLACE_ID used.
    """
    HOME_URL = f"https://pcmap.place.naver.com/restaurant/{place_id}/home"

    if tab_name == "blog":
        anchor_href = "/review/ugc"
        op_name     = "getFsasReviews"
        gql_key     = "fsasReviews"
        fallback_url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/ugc"
    else:
        anchor_href = "/review/visitor"
        op_name     = "getVisitorReviews"
        gql_key     = "visitorReviews"
        fallback_url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor"

    items_raw    = []   # accumulated, may have dupes
    click_count  = 0
    api_total    = None
    partial_reason = None
    sort_html_raw  = None
    consecutive_empty = 0

    log_fn(f"\n{'='*55}")
    log_fn(f"=== TAB: {tab_name.upper()} | op={op_name} ===")
    log_fn(f"{'='*55}")

    # ── Navigate home ──
    nav_pre = len(all_captures)
    log_fn(f"[NAV] 홈 진입: {HOME_URL}")
    try:
        await page.goto(HOME_URL, wait_until="networkidle", timeout=35_000)
    except PlaywrightTimeoutError:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25_000)
    await page.wait_for_timeout(3_000)

    # ── Click review tab anchor ──
    anchor_sel = f"a[href*='{anchor_href}']"
    try:
        link = page.locator(anchor_sel).first
        await link.wait_for(state="visible", timeout=8_000)
        log_fn(f"[NAV] 앵커 클릭: {anchor_sel}")
        await link.click()
        await page.wait_for_timeout(5_000)
    except Exception as e:
        log_fn(f"[NAV] 앵커 실패({e}) → 직접 goto")
        try:
            await page.goto(fallback_url, wait_until="networkidle", timeout=30_000)
        except PlaywrightTimeoutError:
            await page.goto(fallback_url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(5_000)

    # ── Click 최신순 ──
    pre_sort = len(all_captures)
    sort_ok, sort_html_raw = await click_sort_latest(page, log_fn)
    await human_delay(2.5, 3.5)

    # Initial scroll to trigger first visible batch load
    await scroll_bottom(page)
    await asyncio.sleep(3.0)

    # Log all GQL ops detected so far (diagnostic)
    log_gql_ops(all_captures, nav_pre, log_fn)

    # Extract initial batch starting from nav_pre so that getVisitorReviews
    # responses captured during the anchor-fallback goto (indices in
    # [nav_pre, pre_sort)) are included. The id-dedup pass below removes any
    # duplicates that also appear in the later sort re-fetch.
    init_batches = extract_batches(all_captures, nav_pre, op_name, gql_key)
    if init_batches:
        api_total = init_batches[0].get("total")
        for b in init_batches:
            items_raw.extend(b.get("items") or [])
        log_fn(f"[INIT] 초기 배치 {len(init_batches)}개, api_total={api_total}, items={len(items_raw)}")
    else:
        log_fn(f"[INIT] 초기 GQL 캡처 없음")

    prev_cap_idx = len(all_captures)

    # ── Expand loop ──
    log_fn(f"\n[LOOP] 펼쳐서 더보기 루프 시작 (tab={tab_name})")
    loop_iter = 0

    while True:
        loop_iter += 1
        await scroll_bottom(page)
        await human_delay(0.8, 1.5)

        btn = await find_expand_btn(page)
        if btn is None:
            log_fn(f"[LOOP #{loop_iter}] 버튼 없음 → 루프 종료 "
                   f"(clicks={click_count}, items={len(items_raw)})")
            break

        # Click
        click_count += 1
        try:
            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.4)
            raw_btn_text = await btn.inner_text()
            log_fn(f"[CLICK #{click_count}] btn_text={repr(raw_btn_text)}, "
                   f"items_before={len(items_raw)}")
            await btn.click()
        except Exception as e:
            log_fn(f"[CLICK #{click_count}] 클릭 오류: {e}")
            partial_reason = f"click_error at #{click_count}: {e}"
            break

        # Wait for GQL response (§4.2b mandatory per-click wait)
        got_new = await wait_new_gql(
            all_captures, prev_cap_idx, op_name, gql_key, timeout_s=10.0
        )
        if not got_new:
            log_fn(f"[WAIT #{click_count}] 10s timeout — retry once")
            try:
                btn2 = await find_expand_btn(page)
                if btn2:
                    await btn2.click()
                    got_new = await wait_new_gql(
                        all_captures, prev_cap_idx, op_name, gql_key, timeout_s=10.0
                    )
            except Exception as e:
                log_fn(f"[WAIT] retry 클릭 오류: {e}")
            if not got_new:
                log_fn(f"[WAIT #{click_count}] retry 후에도 응답 없음 → 종료")
                partial_reason = f"wait_timeout at click #{click_count} (retry exhausted)"
                break

        # Extract new items from this batch
        new_batches = extract_batches(all_captures, prev_cap_idx, op_name, gql_key)
        prev_cap_idx = len(all_captures)
        new_count = 0
        for b in new_batches:
            its = b.get("items") or []
            items_raw.extend(its)
            new_count += len(its)
            if api_total is None:
                api_total = b.get("total")

        log_fn(f"[CLICK #{click_count}] +{new_count}건, "
               f"누계={len(items_raw)}/{api_total}")

        # Incremental watermark check
        if since_date is not None:
            dates = [
                it.get("representativeVisitDateTime", "")[:10]
                for it in items_raw
                if it.get("representativeVisitDateTime")
            ]
            oldest = min(dates) if dates else None
            if _should_stop_incremental(oldest, since_date):
                log_fn("[INCR] reached watermark, stop")
                break

        # Consecutive-empty guard (server returned items=[] twice → treat as end)
        if new_count == 0:
            consecutive_empty += 1
            log_fn(f"[GUARD] consecutive_empty={consecutive_empty}")
            if consecutive_empty >= 2:
                log_fn(f"[GUARD] 2회 연속 빈 응답 → 서버 한도 도달, 루프 종료")
                break
        else:
            consecutive_empty = 0

        # Rate-limit / error page guard
        cur_url = page.url
        if any(x in cur_url.lower() for x in ["error", "block", "captcha", "robots"]):
            log_fn(f"[RATE] URL 이상 감지: {cur_url}")
            partial_reason = f"rate_limit_url={cur_url} at click #{click_count}"
            break

        await human_delay(1.5, 3.0)

    # ── Dedup ──
    if tab_name == "visitor":
        seen_ids = set()
        items_deduped = []
        for it in items_raw:
            id_ = it.get("id")
            if id_ and id_ in seen_ids:
                continue
            if id_:
                seen_ids.add(id_)
            items_deduped.append(it)
        dupes = len(items_raw) - len(items_deduped)
        if dupes:
            log_fn(f"[DEDUP] visitor: {dupes}건 중복 제거 ({len(items_raw)} → {len(items_deduped)})")
    else:
        seen_blog = set()
        items_deduped = []
        for it in items_raw:
            key = (it.get("authorName"), it.get("date"))
            if key in seen_blog:
                continue
            seen_blog.add(key)
            items_deduped.append(it)
        dupes = len(items_raw) - len(items_deduped)
        if dupes:
            log_fn(f"[DEDUP] blog: {dupes}건 중복 제거 ({len(items_raw)} → {len(items_deduped)})")

    final_count = len(items_deduped)
    oldest = items_deduped[-1] if items_deduped else None
    ref_total = REF_TOTALS.get(tab_name)

    if tab_name == "visitor":
        oldest_date_raw = oldest.get("created") if oldest else None
        oldest_repr_dt  = oldest.get("representativeVisitDateTime") if oldest else None
        oldest_author   = (oldest.get("author") or {}).get("nickname") if oldest else None
    else:
        oldest_date_raw = (
            oldest.get("date") or oldest.get("createdString")
        ) if oldest else None
        oldest_repr_dt = None
        oldest_author  = oldest.get("authorName") if oldest else None

    meta = {
        "tab": tab_name,
        "clicks": click_count,
        "final_count": final_count,
        "raw_count_before_dedup": len(items_raw),
        "api_total": api_total,
        "reference_total": ref_total,
        "count_vs_reference": f"{final_count} vs {ref_total}",
        "oldest_date_raw": oldest_date_raw,
        "oldest_representativeVisitDateTime": oldest_repr_dt,
        "oldest_author": oldest_author,
        "sort_html_raw": sort_html_raw,
        "partial_reason": partial_reason,
    }

    log_fn(f"\n[RESULT {tab_name.upper()}] clicks={click_count}, "
           f"final={final_count}/{ref_total}(ref), "
           f"api_total={api_total}, "
           f"oldest_raw={oldest_date_raw}, "
           f"oldest_author={oldest_author}")
    return items_deduped, meta


# ─────────────────────────── public API ───────────────────────────

async def collect_visitor_items(place_id, since_date=None):
    """Headed Playwright collection of visitor reviews for place_id.
    Returns dict {"items": list[dict], "source_total_count": int|None}."""

    logs = []

    def log_fn(msg=""):
        logs.append(msg)

    all_captures = []

    async def on_resp(response):
        req = response.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        try:
            raw  = await response.body()
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            text = f"[err:{e}]"
        try:
            bj = json.loads(text)
        except Exception:
            bj = None
        all_captures.append({
            "url":       response.url,
            "post_data": req.post_data,
            "body_json": bj,
            "is_gql":    "graphql" in response.url.lower(),
        })

    launch_opts = {
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    }
    if PROXY_URL:
        launch_opts["proxy"] = {"server": PROXY_URL}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_opts)
        try:
            ctx = await browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
            )
            ctx.on("response", on_resp)

            page_v = await ctx.new_page()
            visitor_items, _meta = await process_tab(
                page_v, "visitor", all_captures, log_fn, place_id,
                since_date=since_date,
            )
            await page_v.close()

        finally:
            await browser.close()

    source_total = None
    try:
        raw_total = _meta.get("api_total")
        if raw_total is not None:
            source_total = int(raw_total)
    except (TypeError, ValueError):
        pass

    return {
        "items": [
            {
                "created":                     it.get("created"),
                "representativeVisitDateTime": it.get("representativeVisitDateTime"),
                "visitCount":                  it.get("visitCount"),
                "originType":                  it.get("originType"),
                "author":                      (it.get("author") or {}).get("nickname"),
                "id":                          it.get("id"),
                "has_owner_reply":             bool((it.get("reply") or {}).get("body")),
            }
            for it in visitor_items
        ],
        "source_total_count": source_total,
    }


# ─────────────────────────── peek helper ───────────────────────────

async def _peek_total_count_async(place_id) -> int | None:
    """Light-fetch: navigate visitor tab, capture first getVisitorReviews GQL
    response, return visitorReviews.total. No expand-click or scroll loop."""
    HOME_URL = f"https://pcmap.place.naver.com/restaurant/{place_id}/home"
    fallback_url = f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor"

    all_captures = []

    async def on_resp(response):
        req = response.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        try:
            raw = await response.body()
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return
        try:
            bj = json.loads(text)
        except Exception:
            bj = None
        all_captures.append({
            "url":       response.url,
            "post_data": req.post_data,
            "body_json": bj,
            "is_gql":    "graphql" in response.url.lower(),
        })

    launch_opts = {
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    }
    if PROXY_URL:
        launch_opts["proxy"] = {"server": PROXY_URL}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_opts)
        try:
            ctx = await browser.new_context(
                user_agent=_UA,
                viewport={"width": 1280, "height": 900},
            )
            ctx.on("response", on_resp)
            page = await ctx.new_page()

            try:
                await page.goto(HOME_URL, wait_until="networkidle", timeout=35_000)
            except PlaywrightTimeoutError:
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25_000)
            await page.wait_for_timeout(3_000)

            anchor_sel = "a[href*='/review/visitor']"
            try:
                link = page.locator(anchor_sel).first
                await link.wait_for(state="visible", timeout=8_000)
                await link.click()
                await page.wait_for_timeout(5_000)
            except Exception:
                try:
                    await page.goto(fallback_url, wait_until="networkidle", timeout=30_000)
                except PlaywrightTimeoutError:
                    await page.goto(fallback_url, wait_until="domcontentloaded", timeout=20_000)
                await page.wait_for_timeout(5_000)

            await wait_new_gql(
                all_captures, 0, "getVisitorReviews", "visitorReviews", timeout_s=10.0
            )

            total = None
            batches = extract_batches(all_captures, 0, "getVisitorReviews", "visitorReviews")
            if batches:
                raw_total = batches[0].get("total")
                if raw_total is not None:
                    total = int(raw_total)

            await page.close()
        finally:
            await browser.close()

    return total


def peek_total_count(place_id) -> int | None:
    """Return platform visitor-review total from the first GQL response without
    triggering 펼쳐서 더보기 or any scroll loop. Returns None on any failure."""
    try:
        return asyncio.run(_peek_total_count_async(place_id))
    except Exception:
        return None
