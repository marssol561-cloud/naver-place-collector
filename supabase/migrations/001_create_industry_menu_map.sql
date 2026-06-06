CREATE TABLE industry_menu_map (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  menu       TEXT NOT NULL UNIQUE,
  industry   TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE industry_menu_map ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON industry_menu_map
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_select" ON industry_menu_map
  FOR SELECT TO anon USING (true);
