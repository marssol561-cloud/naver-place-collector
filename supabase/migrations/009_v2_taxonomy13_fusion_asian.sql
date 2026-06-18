-- 009: taxonomy 11->13 (+퓨전 +아시아음식), Naver-category alignment.
-- industry_naver_map: establishment-layer (1st axis)
INSERT INTO industry_naver_map (naver_category, industry)
SELECT v.c, v.i FROM (VALUES
 ('퓨전음식','퓨전'),
 ('아시아음식','아시아음식'),('베트남음식','아시아음식'),('태국음식','아시아음식'),('인도음식','아시아음식')
) AS v(c,i)
WHERE NOT EXISTS (SELECT 1 FROM industry_naver_map n WHERE n.naver_category = v.c);
-- industry_menu_map: clear SE-Asian dishes -> 아시아음식 (NO 퓨전 menu rows)
INSERT INTO industry_menu_map (menu, industry)
SELECT v.m, v.i FROM (VALUES
 ('쌀국수','아시아음식'),('분짜','아시아음식'),('반미','아시아음식'),('월남쌈','아시아음식'),('분보후에','아시아음식'),
 ('팟타이','아시아음식'),('똠얌꿍','아시아음식'),('나시고렝','아시아음식')
) AS v(m,i)
WHERE NOT EXISTS (SELECT 1 FROM industry_menu_map m2 WHERE m2.menu = v.m);
-- ROLLBACK: DELETE the 5 naver rows + 8 menu rows above.
