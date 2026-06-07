-- ============================================================
-- Migration : 005_fix_fn_classify_industry_ambiguity.sql
-- Fix       : DECLARE variable `menu` renamed to `v_menu`
--             to resolve 42702 ambiguous-column-reference error
--             when PostgreSQL sees `WHERE m.menu = menu`
--             (PL/pgSQL variable vs industry_menu_map.menu column)
-- Date      : 2026-06-07
-- Sprint    : SP-8
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

  -- Lookup: array-order first-match via industry_menu_map
  FOREACH v_menu IN ARRAY NEW.signature_menus LOOP
    SELECT m.industry INTO matched
    FROM industry_menu_map m
    WHERE m.menu = v_menu;

    IF matched IS NOT NULL THEN
      NEW.industry := matched;
      RETURN NEW;
    END IF;
  END LOOP;

  -- No match: log unclassified; do NOT write '맛집' fallback
  INSERT INTO industry_unclassified_log (source, input_value, store_id)
  VALUES (
    'trigger',
    array_to_string(NEW.signature_menus, ','),
    CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE NEW.store_id END
  );

  RETURN NEW;
END;
$$;
