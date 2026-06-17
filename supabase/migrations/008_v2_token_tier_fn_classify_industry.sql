-- ============================================================
-- Migration : 008_v2_token_tier_fn_classify_industry.sql
-- Goal      : Add token-matching Pass 2 to fn_classify_industry
--             so signature names like "벽돌삼겹살" resolve to "고기"
--             via longest-substring match against industry_menu_map.
-- Sprint    : SP-V2-4
-- Date      : 2026-06-18
-- Idempotent: CREATE OR REPLACE — safe to re-run.
--
-- Classification order (unchanged except step 4):
--   Guard 1 : industry non-empty → skip (unchanged)
--   Guard 2 : null/empty menus  → skip (unchanged)
--   Pass 1  : exact menu match  → set (unchanged)
--   Pass 2  : token match       → set (NEW)
--   Miss    : log unclassified  → leave industry empty (unchanged)
--
-- Token rule (matches SP-V2-2 reference):
--   Candidates = map keys ≥2 chars that are substrings of v_menu
--   Winner     = the longest candidate (ties: same industry → ok; conflict → NULL)
--   NULL result = hold; try next menu; if all exhausted → miss
--
-- 42702 guard: ALL column refs qualified (table alias or NEW.).
--   PL/pgSQL vars use v_ prefix to avoid collision with column names.
-- ============================================================

CREATE OR REPLACE FUNCTION public.fn_classify_industry()
  RETURNS trigger
  LANGUAGE plpgsql
AS $$
DECLARE
  v_menu  TEXT;
  matched TEXT;
BEGIN
  -- Guard 1: non-empty industry → never overwrite
  IF NEW.industry IS NOT NULL AND NEW.industry <> '' THEN
    RETURN NEW;
  END IF;

  -- Guard 2: NULL or empty array → nothing to classify
  IF NEW.signature_menus IS NULL OR array_length(NEW.signature_menus, 1) IS NULL THEN
    RETURN NEW;
  END IF;

  -- Pass 1 (exact): array-order first-match via industry_menu_map (unchanged)
  FOREACH v_menu IN ARRAY NEW.signature_menus LOOP
    SELECT m.industry INTO matched
    FROM industry_menu_map m
    WHERE m.menu = v_menu;

    IF matched IS NOT NULL THEN
      NEW.industry := matched;
      RETURN NEW;
    END IF;
  END LOOP;

  -- Pass 2 (token): longest-substring match; conflict among longest → NULL → try next
  FOREACH v_menu IN ARRAY NEW.signature_menus LOOP
    WITH cand AS (
      SELECT mm.industry,
             char_length(mm.menu) AS l
      FROM   industry_menu_map mm
      WHERE  char_length(mm.menu) >= 2
        AND  position(mm.menu IN v_menu) > 0
    ),
    longest AS (
      SELECT c.industry
      FROM   cand c
      WHERE  c.l = (SELECT max(c2.l) FROM cand c2)
    )
    SELECT CASE WHEN count(DISTINCT lng.industry) = 1
                THEN max(lng.industry)
                ELSE NULL
           END
    INTO   matched
    FROM   longest lng;

    IF matched IS NOT NULL THEN
      NEW.industry := matched;
      RETURN NEW;
    END IF;
  END LOOP;

  -- Miss: log unclassified; do NOT write '맛집' fallback (unchanged)
  INSERT INTO industry_unclassified_log (source, input_value, store_id)
  VALUES (
    'trigger',
    array_to_string(NEW.signature_menus, ','),
    CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE NEW.store_id END
  );

  RETURN NEW;
END;
$$;
