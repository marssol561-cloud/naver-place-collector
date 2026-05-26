"""
6-5: 배포 환경 동작 확인 스크립트
"""
import sys, os, json, time, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding='utf-8')

BASE = "https://naver-place-collector-production.up.railway.app"
API_KEY = "itdalab_collect_e53fdc32-9b9e-483f-90c7-53a0b7888589"


def http_get(path, timeout=15):
    url = BASE + path
    req = urllib.request.Request(url)
    r = urllib.request.urlopen(req, timeout=timeout)
    body = r.read().decode('utf-8')
    return r.status, body


def http_post(path, body_dict, headers=None, timeout=120):
    url = BASE + path
    data = json.dumps(body_dict).encode('utf-8')
    req_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        body = r.read().decode('utf-8')
        return r.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        return e.code, json.loads(body) if body else {}


print("=" * 60)
print("6-5 배포 환경 동작 확인")
print(f"대상: {BASE}")
print("=" * 60)

# ── 1. GET /health → 200 ─────────────────────────────────────
print("\n[1] GET /health")
status, body = http_get("/health")
print(f"  HTTP {status}: {body}")
assert status == 200 and '"ok"' in body, f"FAIL: {status} {body}"
print("  ✅ PASS")

# ── 2. 인증 없이 → 401 ────────────────────────────────────────
print("\n[2] 인증 없이 → 401")
data = json.dumps({"store_name": "테스트", "address": "서울 강남구"}).encode('utf-8')
req = urllib.request.Request(BASE + "/api/v1/collect", data=data,
                              headers={"Content-Type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=10)
    print("  ❌ FAIL: 401 예상했으나 성공")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}: {e.read().decode()[:60]}")
    assert e.code == 401, f"FAIL: {e.code}"
    print("  ✅ PASS")

# ── 3. UC-1 신규 수집 ────────────────────────────────────────
print("\n[3] UC-1 신규 수집 (최대 120초)")
t0 = time.time()
status, data = http_post("/api/v1/collect",
                          {"store_name": "스타벅스 역삼R점", "address": "서울 강남구 역삼동", "force_refresh": True},
                          timeout=150)
elapsed = time.time() - t0
print(f"  HTTP {status} | {elapsed:.1f}초")
print(f"  응답: {json.dumps(data, ensure_ascii=False)[:200]}")
assert status == 200, f"FAIL: {status}"
assert data.get("status") in ("collected", "refreshed", "already_exists"), f"FAIL status: {data}"
store_id_uc1 = data.get("store_id")
place_id_uc1 = data.get("place_id")
region_uc1 = None

# region 확인은 DB에서 직접
import requests as req_lib
from dotenv import load_dotenv
load_dotenv()
DB_URL = os.getenv("MASTER_DB_URL")
DB_KEY = os.getenv("MASTER_DB_SERVICE_ROLE_KEY")
db_h = {"apikey": DB_KEY, "Authorization": f"Bearer {DB_KEY}"}
if store_id_uc1:
    row = req_lib.get(f"{DB_URL}/rest/v1/stores",
                      params={"store_id": f"eq.{store_id_uc1}",
                              "select": "store_id,place_id,store_name,address,region,rating,total_reviews"},
                      headers=db_h).json()
    if row:
        region_uc1 = row[0].get("region")
        print(f"  마스터DB: store_id={row[0].get('store_id')[:8]}... region={region_uc1} "
              f"rating={row[0].get('rating')} total_reviews={row[0].get('total_reviews')}")

print(f"  ✅ PASS — status={data.get('status')}, region={region_uc1}")

# ── 4. already_exists ────────────────────────────────────────
print("\n[4] already_exists (동일 place_id 재요청)")
if place_id_uc1:
    t0 = time.time()
    status, data = http_post("/api/v1/collect", {"place_id": place_id_uc1}, timeout=30)
    elapsed = time.time() - t0
    print(f"  HTTP {status} | {elapsed:.2f}초")
    print(f"  응답: {json.dumps(data, ensure_ascii=False)}")
    assert data.get("status") == "already_exists", f"FAIL: {data}"
    print(f"  ✅ PASS — already_exists {elapsed:.2f}초")
else:
    print("  ⚠️ SKIP (place_id 없음)")

# ── 5. 미등록 점포 ────────────────────────────────────────────
print("\n[5] 미등록 점포 → collected_without_place (최대 60초)")
t0 = time.time()
status, data = http_post("/api/v1/collect",
                          {"store_name": "없는점포Railway9999", "address": "서울 강남구 역삼동 999-99"},
                          timeout=90)
elapsed = time.time() - t0
print(f"  HTTP {status} | {elapsed:.1f}초")
print(f"  응답: {json.dumps(data, ensure_ascii=False)[:200]}")
assert status == 200, f"FAIL: {status}"
assert data.get("status") == "collected_without_place", f"FAIL: {data}"
assert data.get("place_id") is None, f"FAIL place_id: {data}"
print(f"  ✅ PASS — collected_without_place, place_id=null")

# ── 6. 한글 표시 확인 ────────────────────────────────────────
print("\n[6] 한글 표시 확인")
if store_id_uc1 and region_uc1:
    korean_ok = all(ord(c) < 0x10000 for c in (region_uc1 or ""))
    has_korean = any(0xAC00 <= ord(c) <= 0xD7A3 for c in (region_uc1 or ""))
    print(f"  region={region_uc1} (한글 포함: {has_korean})")
    assert has_korean, f"FAIL: 한글 없음 region={region_uc1}"
    print("  ✅ PASS — 한글 정상 표시")
else:
    print("  ⚠️ SKIP")

print("\n" + "=" * 60)
print("6-5 배포 환경 동작 확인 완료")
print("=" * 60)
