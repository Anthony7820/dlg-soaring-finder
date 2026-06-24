-- ============================================================
-- DLG Thermal & Slope Finder — community flight log (Supabase)
-- ============================================================
-- HOW TO TURN ON THE SHARED LOG + LEADERBOARD:
--   1. Create a free Supabase project (supabase.com).
--   2. Open the project's SQL Editor and run this whole file.
--   3. In Project Settings -> API, copy the "Project URL" and the
--      "anon public" key.
--   4. In thermal-finder.html / index.html, set near the top of the
--      script:  const SUPA_URL='https://xxxx.supabase.co', SUPA_KEY='eyJ...';
--   5. Re-deploy. The leaderboard switches from "your flights only" to
--      the community board, and logging a flight posts it for everyone.
--
-- The anon public key is safe to ship in the browser: all access is
-- constrained by the row-level-security policies below (read-all,
-- insert-only with sanity bounds, no update/delete for anonymous users).
-- ============================================================

create table if not exists public.flights (
  id           bigint generated always as identity primary key,
  created_at   timestamptz not null default now(),
  pilot        text not null default 'anon',
  lat          double precision not null,
  lon          double precision not null,
  date         date,
  aircraft     text,
  max_alt_ft   real,
  max_time_min real,
  notes        text
);

-- helpful index for the leaderboard ordering
create index if not exists flights_created_idx on public.flights (created_at desc);

alter table public.flights enable row level security;

-- anyone can read the board
drop policy if exists "flights public read" on public.flights;
create policy "flights public read"
  on public.flights for select
  using (true);

-- anyone can add a flight (no login yet) — with basic sanity bounds
drop policy if exists "flights anon insert" on public.flights;
create policy "flights anon insert"
  on public.flights for insert
  with check (
    lat between -90 and 90
    and lon between -180 and 180
    and char_length(coalesce(pilot, '')) <= 32
    and char_length(coalesce(aircraft, '')) <= 40
    and char_length(coalesce(notes, '')) <= 300
  );

-- NOTE: no update or delete policy is defined, so anonymous users
-- cannot edit or remove flights. When you add sign-in later, add an
-- owner column + policies so pilots can manage their own entries.
