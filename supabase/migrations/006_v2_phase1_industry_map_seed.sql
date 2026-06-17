-- 006_v2_phase1_industry_map_seed.sql

-- V2 Phase 1: poultry/fish reclassification + seafood/chicken seed + 술집 naver categories.

-- Idempotent (safe to re-run). DB already contains these rows as of 2026-06-17.
-- FORWARD

UPDATE industry_menu_map SET industry='한식' WHERE menu='닭볶음탕';

UPDATE industry_menu_map SET industry='일식' WHERE menu='회';

UPDATE industry_menu_map SET industry='일식' WHERE menu='참치';
INSERT INTO industry_menu_map (menu, industry)

SELECT v.menu, v.industry FROM (VALUES

('닭도리탕','한식'),('백숙','한식'),('닭갈비','한식'),

('숯불치킨','치킨'),('바베큐치킨','치킨'),

('생선회','일식'),('물회','일식'),

('명태조림','해산물'),('조기매운탕','해산물'),('매운탕','해산물'),

('고등어조림','해산물'),('고등어구이','해산물'),('조기구이','해산물'),

('갈치조림','해산물'),('갈치구이','해산물'),('삼치구이','해산물')

) AS v(menu, industry)

WHERE NOT EXISTS (SELECT 1 FROM industry_menu_map m WHERE m.menu = v.menu);
UPDATE industry_naver_map SET industry='일식' WHERE naver_category='생선회';
INSERT INTO industry_naver_map (naver_category, industry)

SELECT v.naver_category, v.industry FROM (VALUES

('호프,통닭','술집'),('이자카야','술집'),('요리주점','술집'),('술집','술집'),('포장마차','술집')

) AS v(naver_category, industry)

WHERE NOT EXISTS (SELECT 1 FROM industry_naver_map n WHERE n.naver_category = v.naver_category);
-- ROLLBACK (manual; run if breakage)

-- UPDATE industry_menu_map SET industry='치킨'   WHERE menu='닭볶음탕';

-- UPDATE industry_menu_map SET industry='해산물' WHERE menu IN ('회','참치');

-- DELETE FROM industry_menu_map WHERE menu IN ('닭도리탕','백숙','닭갈비','숯불치킨','바베큐치킨','생선회','물회','명태조림','조기매운탕','매운탕','고등어조림','고등어구이','조기구이','갈치조림','갈치구이','삼치구이');

-- UPDATE industry_naver_map SET industry='해산물' WHERE naver_category='생선회';

-- DELETE FROM industry_naver_map WHERE naver_category IN ('호프,통닭','이자카야','요리주점','술집','포장마차');
