CREATE TABLE industry_unclassified_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source      TEXT NOT NULL CHECK (source IN ('backend','crawler','trigger','flutter')),
  input_value TEXT NOT NULL,
  store_id    UUID REFERENCES stores(store_id) ON DELETE SET NULL,
  occurred_at TIMESTAMPTZ DEFAULT NOW(),
  resolved    BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE industry_unclassified_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all" ON industry_unclassified_log
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "anon_select" ON industry_unclassified_log
  FOR SELECT TO anon USING (true);
