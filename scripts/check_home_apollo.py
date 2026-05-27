"""
검증: 홈 탭 Apollo State에서 good_point_votes / menu_mentions / feature_mentions 존재 여부
"""
import asyncio
import json
import re
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

PLACE_ID = "1446535451"
BASE_URL = f"https://pcmap.place.naver.com/restaurant/{PLACE_ID}"

async def main():
    print(f"=== HOME 탭 Apollo State 검증 ===")
    print(f"URL: {BASE_URL}/home\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print("[1] 홈 탭 로딩...")
        await page.goto(f"{BASE_URL}/home", wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(3000)

        entry_frame = None
        for frame in page.frames:
            if PLACE_ID in frame.url:
                entry_frame = frame
                break
        if entry_frame is None:
            entry_frame = page

        body_text = await entry_frame.locator("body").inner_text(timeout=5000)
        print(f"[2] body_text 길이: {len(body_text):,}자")
        print(f"    첫 100자: {body_text[:100]!r}")

        if "서비스 이용이 제한" in body_text or len(body_text) < 200:
            print("    ⚠️  접근 차단 페이지 — Apollo State만 분석")
        else:
            print("    ✅ 정상 로딩")

        html_content = await entry_frame.content()
        print(f"[3] html_content 길이: {len(html_content):,}자")

        # Apollo State 추출
        apollo_match = re.search(
            r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});",
            html_content,
            re.DOTALL,
        )
        if not apollo_match:
            print("    ❌ Apollo State 없음")
            await browser.close()
            return

        apollo_raw = apollo_match.group(1)
        print(f"[4] Apollo State: {len(apollo_raw):,}자")

        # 저장
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "home_apollo_state.json")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(apollo_raw)
        print(f"    [저장] home_apollo_state.json")

        # VisitorReviewStatsResult 존재 여부
        print("\n[5] good_point_votes / menu_mentions 확인")
        has_visitor_review = "VisitorReviewStatsResult" in apollo_raw
        print(f"    VisitorReviewStatsResult: {'✅ 있음' if has_visitor_review else '❌ 없음'}")

        if has_visitor_review:
            # votedKeyword 확인
            has_voted = "votedKeyword" in apollo_raw
            has_menus = '"menus":[{' in apollo_raw
            print(f"    votedKeyword (good_point_votes): {'✅ 있음' if has_voted else '❌ 없음'}")
            print(f"    menus[] (menu_mentions): {'✅ 있음' if has_menus else '❌ 없음'}")

            # 샘플 값 출력
            m = re.search(r'"displayName"\s*:\s*"([^"]+)","count"\s*:\s*(\d+)', apollo_raw)
            if m:
                print(f"    good_point 샘플: {m.group(1)} = {m.group(2)}")

            m2 = re.search(
                r'"VisitorReviewStatsResult:[^"]+"\s*:\s*\{[^}]*"analysis"\s*:\s*\{[^}]*"menus"\s*:\s*\[([^\]]+)\]',
                apollo_raw,
                re.DOTALL,
            )
            # simpler approach
            idx = apollo_raw.find('"menus":[{"__typename":"VisitorReviewStatsAnalysisThemes"')
            if idx >= 0:
                snippet = apollo_raw[idx:idx+200]
                m3 = re.search(r'"label"\s*:\s*"([^"]+)","count"\s*:\s*(\d+)', snippet)
                if m3:
                    print(f"    menu_mentions 샘플: {m3.group(1)} = {m3.group(2)}")

        # InformationFacilities 존재 여부
        print("\n[6] feature_mentions (InformationFacilities) 확인")
        has_facilities = "InformationFacilities" in apollo_raw
        print(f"    InformationFacilities: {'✅ 있음' if has_facilities else '❌ 없음'}")

        if has_facilities:
            # 시설 이름 추출
            facility_names = re.findall(
                r'"InformationFacilities:\d+"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                apollo_raw,
            )
            print(f"    시설 목록: {facility_names}")

            # InformationTab.facilities 확인
            has_info_tab = "InformationTab" in apollo_raw
            print(f"    InformationTab: {'✅ 있음' if has_info_tab else '❌ 없음'}")

        # InformationParking 확인
        print("\n[7] parking (InformationParking) 확인")
        has_parking = "InformationParking" in apollo_raw
        print(f"    InformationParking: {'✅ 있음' if has_parking else '❌ 없음'}")

        if has_parking:
            m = re.search(
                r'"InformationParking"\s*,\s*"description"\s*:\s*([^,]+)',
                apollo_raw,
            )
            idx = apollo_raw.find("InformationParking")
            snippet = apollo_raw[max(0, idx-20):idx+300]
            print(f"    parkingInfo snippet: {snippet!r}")

        # keywordList 확인
        print("\n[8] keywordList 확인")
        kw_m = re.search(r'"keywordList"\s*:\s*\[([^\]]+)\]', apollo_raw)
        if kw_m:
            print(f"    keywordList: ✅ {kw_m.group(1)[:200]}")
        else:
            print(f"    keywordList: ❌ 없음")

        await browser.close()

    print("\n=== 검증 완료 ===")


if __name__ == "__main__":
    asyncio.run(main())
