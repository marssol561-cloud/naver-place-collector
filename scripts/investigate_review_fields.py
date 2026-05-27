"""
조사 스크립트: good_point_votes / feature_mentions / menu_mentions
동암상회 place_id: 1446535451
/review 탭 HTML 구조 분석 — 코드 수정 없이 조사만
"""

import asyncio
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

PLACE_ID = "1446535451"
BASE_URL = f"https://pcmap.place.naver.com/restaurant/{PLACE_ID}"
REVIEW_URL = f"{BASE_URL}/review"

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


async def main():
    print(f"=== 조사 시작: {PLACE_ID} ===")
    print(f"URL: {REVIEW_URL}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # GQL 응답 수집
        gql_responses = []

        async def on_response(resp):
            if "graphql" in resp.url.lower() or "map.naver.com" in resp.url:
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct:
                        data = await resp.json()
                        gql_responses.append({"url": resp.url, "data": data})
                except Exception:
                    pass

        page.on("response", on_response)

        # 1단계: 홈 탭 먼저 로드 (SPA 초기화)
        print("[1] 홈 탭 로딩...")
        await page.goto(f"{BASE_URL}/home", wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(3000)

        # entry_frame 찾기
        entry_frame = None
        for frame in page.frames:
            if PLACE_ID in frame.url:
                entry_frame = frame
                break

        if entry_frame is None:
            entry_frame = page

        print(f"   entry_frame URL: {entry_frame.url}")

        # 2단계: /review 탭으로 이동
        print("[2] /review 탭으로 이동...")
        await entry_frame.goto(REVIEW_URL, wait_until="networkidle", timeout=20_000)
        await page.wait_for_timeout(3000)

        # 추가 대기 (동적 콘텐츠 로드)
        await page.wait_for_timeout(2000)

        # 3단계: body_text 덤프
        print("[3] body_text 추출...")
        body_text = await entry_frame.locator("body").inner_text(timeout=10000)
        body_preview = body_text[:3000]
        print(f"--- body_text (첫 3000자) ---\n{body_preview}\n")

        # body_text 파일로 저장
        with open(os.path.join(OUTPUT_DIR, "review_body_text.txt"), "w", encoding="utf-8") as f:
            f.write(body_text)
        print(f"   [저장] review_body_text.txt ({len(body_text):,}자)")

        # 4단계: html_content 덤프 (Apollo State 포함)
        print("[4] html_content 추출...")
        html_content = await entry_frame.content()

        # Apollo State 추출
        apollo_match = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", html_content, re.DOTALL)
        if apollo_match:
            apollo_raw = apollo_match.group(1)
            print(f"   Apollo State 발견 ({len(apollo_raw):,}자)")

            # Apollo State 파일 저장
            with open(os.path.join(OUTPUT_DIR, "review_apollo_state.json"), "w", encoding="utf-8") as f:
                f.write(apollo_raw)
            print(f"   [저장] review_apollo_state.json")

            # 주요 키 탐색
            try:
                apollo_data = json.loads(apollo_raw)
                print(f"\n   Apollo State 최상위 키 ({len(apollo_data)} 개):")
                for k in list(apollo_data.keys())[:30]:
                    print(f"     - {k}")
            except json.JSONDecodeError as e:
                print(f"   JSON 파싱 오류: {e}")
        else:
            print("   Apollo State 없음")

        # html_content 파일 저장
        with open(os.path.join(OUTPUT_DIR, "review_html.html"), "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"   [저장] review_html.html ({len(html_content):,}자)")

        # 5단계: good_point_votes 조사 ("이런 점이 좋았어요")
        print("\n[5] good_point_votes 조사 ('이런 점이 좋았어요')")

        # 5-1: body_text에서 좋았어요 섹션 탐색
        lines = body_text.split("\n")
        good_point_idx = None
        for i, line in enumerate(lines):
            if "이런 점이 좋았어요" in line or "좋았어요" in line:
                good_point_idx = i
                print(f"   ✅ body_text 라인 {i}: '{line.strip()}'")
                # 주변 20줄 출력
                start = max(0, i - 2)
                end = min(len(lines), i + 25)
                print(f"   [컨텍스트 {start}~{end}]:")
                for j in range(start, end):
                    print(f"     [{j}] {lines[j]!r}")
                break

        if good_point_idx is None:
            print("   ❌ body_text에 '이런 점이 좋았어요' 없음")

        # 5-2: Apollo State에서 goodPointVotes / visitScore 패턴 탐색
        patterns_to_check = [
            r'"goodPoint[^"]*"\s*:\s*\{[^}]{0,500}\}',
            r'"good_point[^"]*"\s*:\s*',
            r'"visitScore[^"]*"\s*:\s*',
            r'"visitKeyword[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"reviewKeyword[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"positiveKeyword[^"]*"\s*:\s*',
            r'"helpfulKeyword[^"]*"\s*:\s*',
            r'"highlight[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"likeCount"\s*:\s*\d+',
            r'"voteCount"\s*:\s*\d+',
            r'"count"\s*:\s*\d+.*?"keyword"',
        ]

        print("\n   Apollo State 패턴 탐색:")
        for pat in patterns_to_check:
            m = re.search(pat, html_content)
            if m:
                snippet = m.group(0)[:200]
                print(f"   ✅ 패턴 '{pat[:40]}...' → {snippet!r}")
            else:
                print(f"   ❌ 패턴 '{pat[:40]}...' → 없음")

        # 5-3: HTML 셀렉터 탐색 (DOM 구조)
        print("\n   DOM 셀렉터 탐색 ('이런 점이 좋았어요'):")
        selectors_to_try = [
            "[class*='KeywordScore']",
            "[class*='keyword_score']",
            "[class*='goodPoint']",
            "[class*='good_point']",
            "[class*='visitScore']",
            "[class*='reviewKeyword']",
            "[class*='VisitKeyword']",
            "[class*='highlight']",
            "[class*='Highlight']",
        ]
        for sel in selectors_to_try:
            try:
                els = await entry_frame.locator(sel).all()
                if els:
                    print(f"   ✅ {sel} → {len(els)}개")
                    # 첫 번째 요소 텍스트
                    try:
                        txt = await els[0].inner_text(timeout=3000)
                        print(f"      첫 번째: {txt[:100]!r}")
                    except Exception:
                        pass
                else:
                    print(f"   ❌ {sel} → 0개")
            except Exception as e:
                print(f"   ⚠️  {sel} → 오류: {e}")

        # 6단계: menu_mentions 조사
        print("\n[6] menu_mentions 조사 (메뉴 언급수)")

        # 6-1: body_text에서 메뉴 언급 탐색
        menu_keywords = ["고기", "갈오징어", "삼겹살", "메뉴"]
        for kw in menu_keywords:
            for i, line in enumerate(lines):
                if kw in line and any(c.isdigit() for c in line):
                    print(f"   ✅ '{kw}' body_text 라인 {i}: {line.strip()!r}")
                    # 주변 3줄
                    for j in range(max(0, i-1), min(len(lines), i+4)):
                        print(f"     [{j}] {lines[j]!r}")
                    break

        # 6-2: Apollo State 메뉴 관련 패턴
        menu_patterns = [
            r'"menuMention[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"menuStat[^"]*"\s*:\s*',
            r'"menuKeyword[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"orderMenuStat[^"]*"\s*:\s*\[([^\]]{0,1000})\]',
            r'"menuCount"\s*:\s*\d+',
            r'"menuName"\s*:\s*"[^"]{1,30}"',
        ]

        print("\n   Apollo State 메뉴 패턴 탐색:")
        for pat in menu_patterns:
            m = re.search(pat, html_content)
            if m:
                snippet = m.group(0)[:200]
                print(f"   ✅ 패턴 '{pat[:40]}...' → {snippet!r}")
            else:
                print(f"   ❌ 패턴 '{pat[:40]}...' → 없음")

        # 7단계: feature_mentions 조사
        print("\n[7] feature_mentions 조사")
        feature_kws = ["편의시설", "주차", "예약", "단체", "무선 인터넷", "포장", "배달"]
        for kw in feature_kws:
            in_body = kw in body_text
            print(f"   '{kw}' in body_text: {'✅' if in_body else '❌'}")

        # feature 관련 Apollo 패턴
        feature_patterns = [
            r'"facilitie[^"]*"\s*:\s*',
            r'"amenity[^"]*"\s*:\s*',
            r'"convenience[^"]*"\s*:\s*',
            r'"service[^"]*"\s*:\s*\[([^\]]{0,500})\]',
            r'"featureMention[^"]*"\s*:\s*',
        ]

        print("\n   Apollo State feature 패턴 탐색:")
        for pat in feature_patterns:
            m = re.search(pat, html_content)
            if m:
                snippet = m.group(0)[:200]
                print(f"   ✅ 패턴 '{pat[:40]}...' → {snippet!r}")
            else:
                print(f"   ❌ 패턴 '{pat[:40]}...' → 없음")

        # 8단계: 리뷰 탭 전체 구조 파악 (섹션 헤더 탐색)
        print("\n[8] 리뷰 탭 섹션 헤더 탐색")
        section_patterns = [
            "이런 점이 좋았어요",
            "방문자 리뷰",
            "블로그 리뷰",
            "메뉴",
            "사진",
            "키워드",
            "한줄평",
        ]
        for sp in section_patterns:
            found = sp in body_text
            print(f"   '{sp}': {'✅ 있음' if found else '❌ 없음'}")

        # 9단계: GQL 응답에서 관련 데이터 탐색
        print(f"\n[9] GQL 응답 분석 ({len(gql_responses)}개 수집)")
        for i, resp in enumerate(gql_responses[:5]):
            print(f"   응답 {i}: {resp['url'][:80]}")
            data_str = json.dumps(resp['data'])
            for kw in ["goodPoint", "keywordScore", "menuMention", "visitKeyword"]:
                if kw.lower() in data_str.lower():
                    print(f"     ✅ '{kw}' 포함")

        # 10단계: "이런 점이 좋았어요" 전체 블록 HTML 추출
        print("\n[10] '이런 점이 좋았어요' HTML 블록 추출 시도")
        try:
            # 텍스트로 요소 찾기
            good_el = entry_frame.get_by_text("이런 점이 좋았어요", exact=False)
            count = await good_el.count()
            print(f"   '이런 점이 좋았어요' 텍스트 요소: {count}개")
            if count > 0:
                # 부모 컨테이너 HTML
                parent_html = await good_el.first.evaluate(
                    "el => el.closest('section') ? el.closest('section').outerHTML : el.parentElement.parentElement.outerHTML"
                )
                print(f"   부모 HTML (첫 1500자):\n{parent_html[:1500]}")
                with open(os.path.join(OUTPUT_DIR, "good_point_section.html"), "w", encoding="utf-8") as f:
                    f.write(parent_html)
                print(f"   [저장] good_point_section.html")
        except Exception as e:
            print(f"   ⚠️  오류: {e}")

        # 11단계: 전체 리뷰 탭 스크롤 후 재추출
        print("\n[11] 스크롤 후 재추출")
        try:
            await entry_frame.evaluate("window.scrollTo(0, 500)")
            await page.wait_for_timeout(1000)
            await entry_frame.evaluate("window.scrollTo(0, 1000)")
            await page.wait_for_timeout(1000)

            body_text2 = await entry_frame.locator("body").inner_text(timeout=5000)
            if len(body_text2) > len(body_text):
                print(f"   스크롤 후 body_text 증가: {len(body_text):,} → {len(body_text2):,}자")
                # 새로 나온 부분만 체크
                for sp in section_patterns:
                    if sp not in body_text and sp in body_text2:
                        print(f"   ✅ 스크롤 후 새로 나타남: '{sp}'")
            else:
                print(f"   스크롤 후 변화 없음 ({len(body_text2):,}자)")
        except Exception as e:
            print(f"   ⚠️  스크롤 오류: {e}")

        await browser.close()

    print("\n=== 조사 완료 ===")


if __name__ == "__main__":
    asyncio.run(main())
