CREATE TABLE industry_naver_map (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  naver_category  TEXT NOT NULL UNIQUE,
  industry        TEXT NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE industry_naver_map ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON industry_naver_map
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_select" ON industry_naver_map
  FOR SELECT TO anon USING (true);
