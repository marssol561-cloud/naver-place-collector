"""
ISSUE1: extract_facility_services unit tests

Fixtures:
  tests/fixtures/facilities_section_1418626719.html
    — rendered section HTML captured 2026-06-13 from 생선전문명가 부천북부역점 /information tab.
    Contains '편의시설 및 서비스' section (8 items) + next sibling section.

  tests/fixtures/facilities_section_1709413013.html
    — rendered section HTML from 어반정원 (6 items, regression guard).

  tests/fixtures/facilities_with_payment_section.html
    — 생선전문명가 facilities section + synthetic '결제수단' section with '간편결제'.
    Proves adjacent-section exclusion.

Run: python test_issue1_facility_services.py
"""
import sys, os, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collector.place_crawler import extract_facility_services

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures")

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(label, cond, detail=""):
    status = PASS if cond else FAIL
    msg = f"[{status}] {label}" + (f"\n         detail: {detail}" if detail and not cond else "")
    results.append((status, msg))
    print(msg)
    return cond


def load(filename):
    with open(os.path.join(FIXTURES, filename), encoding="utf-8") as f:
        return f.read()


# ── T1: empty / not-found inputs ─────────────────────────────────────────────
print("\n=== T1: empty / not-found inputs ===")

check("T1-a empty string → []", extract_facility_services("") == [])
check("T1-b no section → []", extract_facility_services("<html><body><p>hello</p></body></html>") == [])
check("T1-c None-like empty → []", extract_facility_services("   ") == [])


# ── T2: 생선전문명가 fixture — 8 items exactly ────────────────────────────────
print("\n=== T2: 생선전문명가 (1418626719) — 8 items ===")

html_fish = load("facilities_section_1418626719.html")
result_fish = extract_facility_services(html_fish)

EXPECTED_FISH = [
    "단체 이용 가능",
    "포장",
    "배달",
    "무선 인터넷",
    "남/녀 화장실 구분",
    "유아의자",
    "화장실 휠체어 이용가능",
    "장애인 주차구역",
]

check("T2-a count=8", len(result_fish) == 8, f"got {len(result_fish)}: {result_fish}")
check("T2-b items match exactly", result_fish == EXPECTED_FISH,
      f"got={result_fish}\nexp={EXPECTED_FISH}")
check("T2-c 화장실 휠체어 이용가능 included",
      "화장실 휠체어 이용가능" in result_fish)
check("T2-d 장애인 주차구역 included",
      "장애인 주차구역" in result_fish)


# ── T3: 어반정원 fixture — 6 items (regression) ──────────────────────────────
print("\n=== T3: 어반정원 (1709413013) — 6 items regression ===")

html_garden = load("facilities_section_1709413013.html")
result_garden = extract_facility_services(html_garden)

EXPECTED_GARDEN = [
    "단체 이용 가능",
    "포장",
    "유아의자",
    "남/녀 화장실 구분",
    "무선 인터넷",
    "예약",
]

check("T3-a count=6", len(result_garden) == 6, f"got {len(result_garden)}: {result_garden}")
check("T3-b items match exactly", result_garden == EXPECTED_GARDEN,
      f"got={result_garden}\nexp={EXPECTED_GARDEN}")


# ── T4: adjacent payment section NOT included ─────────────────────────────────
print("\n=== T4: adjacent '간편결제' section excluded ===")

html_with_pay = load("facilities_with_payment_section.html")
result_pay = extract_facility_services(html_with_pay)

check("T4-a 간편결제 NOT in result", "간편결제" not in result_pay,
      f"result contained: {result_pay}")
check("T4-b 신용카드 NOT in result", "신용카드" not in result_pay,
      f"result contained: {result_pay}")
check("T4-c facilities still 8", len(result_pay) == 8,
      f"got {len(result_pay)}: {result_pay}")
check("T4-d 화장실 휠체어 still present", "화장실 휠체어 이용가능" in result_pay)
check("T4-e 장애인 주차구역 still present", "장애인 주차구역" in result_pay)


# ── T5: synthetic minimal fixture ────────────────────────────────────────────
print("\n=== T5: synthetic minimal HTML ===")

MINIMAL = """<body>
<div class="place_section no_margin ABC">
  <h2 class="place_section_header">
    <div class="place_section_header_title">편의시설 및 서비스<em class="place_section_count">3</em></div>
  </h2>
  <div class="place_section_content">
    <ul>
      <li><svg aria-hidden="true"><path d="M0 0"/></svg><div>무선 인터넷</div></li>
      <li><svg aria-hidden="true"><path d="M0 0"/></svg><div>포장</div></li>
      <li><svg aria-hidden="true"><path d="M0 0"/></svg><div>단체 이용 가능</div></li>
    </ul>
  </div>
</div>
<div class="place_section no_margin DEF">
  <h2 class="place_section_header">
    <div class="place_section_header_title">결제수단<em class="place_section_count">1</em></div>
  </h2>
  <div class="place_section_content">
    <ul>
      <li><svg aria-hidden="true"><path d="M0 0"/></svg><div>간편결제</div></li>
    </ul>
  </div>
</div>
</body>"""

result_minimal = extract_facility_services(MINIMAL)
check("T5-a minimal: 3 items", len(result_minimal) == 3,
      f"got {len(result_minimal)}: {result_minimal}")
check("T5-b minimal: correct labels",
      result_minimal == ["무선 인터넷", "포장", "단체 이용 가능"],
      f"got {result_minimal}")
check("T5-c minimal: 간편결제 excluded", "간편결제" not in result_minimal)


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for s, _ in results if s == PASS)
failed = sum(1 for s, _ in results if s == FAIL)
print(f"TOTAL: {passed} PASS / {failed} FAIL")
if failed:
    print("FAILED tests:")
    for s, msg in results:
        if s == FAIL:
            print(f"  {msg}")
    sys.exit(1)
else:
    print("ALL PASS")
