CREATE TABLE IF NOT EXISTS public.store_visitor_reviews (
  store_id                  uuid PRIMARY KEY REFERENCES public.stores(store_id) ON DELETE CASCADE,
  place_id                  text,
  total_count               integer,
  receipt_count             integer,
  first_review_date         text,
  distinct_review_days      integer,
  daily_average_reviews     double precision,
  revisit_count             integer,
  revisit_ratio             double precision,
  revisit_distribution      jsonb,
  reply_count               integer,
  owner_receipt_reply_rate  double precision,
  daily_counts              jsonb,
  captured_at               timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.store_visitor_reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON public.store_visitor_reviews
  FOR ALL USING (true) WITH CHECK (true);
