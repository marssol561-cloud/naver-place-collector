-- SP-1c: Naver category normalization seed
-- Source: itdalab-infra stores.crawl_data->>'category' (356 stores total)
-- Extracted: 13 stores with non-empty category, 9 distinct strings
-- Resolved: 8  |  UNRESOLVED: 1 (주꾸미요리 — no rule match)
-- Baseline unclassified rate: 1/13 = 7.7%
-- NULL/empty category stores: 343 (96.3% of total)
--
-- Rule applied (priority 1→11, first-match substring unless noted):
--   P1=고기  P2=해산물  P3=중식  P4=일식  P5=양식
--   P6=치킨  P7=베이커리  P8=술집  P9=분식  P10=카페  P11=한식

INSERT INTO industry_naver_map (naver_category, industry) VALUES
  -- 고기 (P1: 고기)
  ('육류,고기요리', '고기'),

  -- 분식 (P9: 만두)
  ('칼국수,만두', '분식'),

  -- 양식 (P5: 브런치 / P5: 양식)
  ('브런치',       '양식'),
  ('양식',         '양식'),

  -- 일식 (P4: 일식)
  ('일식당',       '일식'),

  -- 카페 (P10: 카페)
  ('카페',         '카페'),

  -- 한식 (P11: 한식)
  ('한식',         '한식'),

  -- 해산물 (P2: 해물 + 생선)
  ('해물,생선요리', '해산물')

ON CONFLICT (naver_category) DO UPDATE
  SET industry   = EXCLUDED.industry,
      updated_at = NOW();
