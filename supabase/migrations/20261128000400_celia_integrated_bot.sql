-- ONE integrated Telegram bot (@CeliaFashionBot) for CELIA + approvals.
--
-- Telegram allows a webhook OR getUpdates per bot, never both. So the Edge
-- Function `celia-telegram` receives every update (webhook) and either
--   * answers the owner's chat itself (CELIA_CHAT=instant), or
--   * queues the update in `celia_relay`, where the existing approval runner
--     (scripts/approval_bot_ci.py) reads it exactly like getUpdates — same
--     update ids, same offset semantics, same approval logic, unchanged.
-- Rollback = delete the webhook: the runner falls back to getUpdates on its own.
--
--   * celia_relay   — updates for the approval runner (pruned after 7 days)
--   * celia_config  — the owner allowlist pushed by the authenticated runner
--                     (PA_APPROVAL_ALLOWED_SENDERS), so no second copy of it
--                     has to be typed into Supabase
--   * celia_memory_replace — the runner's PersonalMemory writes full snapshots;
--                     one atomic replace keeps Supabase the ONLY memory store.
-- Server-side only: RLS on, nothing granted to anon/authenticated.

create table if not exists public.celia_relay (
  update_id   bigint primary key,
  payload     jsonb not null check (octet_length(payload::text) < 1000000),
  received_at timestamptz not null default now()
);

create table if not exists public.celia_config (
  key         text primary key check (key in ('owners')),
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);

alter table public.celia_relay  enable row level security;
alter table public.celia_config enable row level security;
revoke all on public.celia_relay, public.celia_config from public, anon, authenticated;
grant select, insert, update, delete on public.celia_relay, public.celia_config to service_role;

-- getUpdates semantics: everything below p_offset is confirmed (deleted);
-- the rest is returned oldest first. A NULL offset only peeks.
create or replace function public.celia_relay_fetch(p_offset bigint, p_limit integer default 100)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  result jsonb;
begin
  if p_offset is not null then
    delete from public.celia_relay where update_id < p_offset;
  end if;
  delete from public.celia_relay where received_at < now() - interval '7 days';
  select coalesce(jsonb_agg(payload order by update_id), '[]'::jsonb) into result
  from (select update_id, payload from public.celia_relay
        order by update_id limit least(greatest(coalesce(p_limit, 100), 1), 100)) q;
  return result;
end;
$$;

-- Replace CELIA's durable memory with the given rows in one transaction.
create or replace function public.celia_memory_replace(p_rows jsonb)
returns integer
language plpgsql security definer set search_path = public as $$
declare
  n integer;
begin
  if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows) > 1000 then
    raise exception 'celia_memory_replace: rows must be an array of at most 1000 items';
  end if;
  if exists (select 1 from jsonb_array_elements(p_rows) r where r->>'id' is null) then
    raise exception 'celia_memory_replace: every row needs an id';
  end if;
  delete from public.celia_memory
  where id not in (select (r->>'id')::integer from jsonb_array_elements(p_rows) r);
  insert into public.celia_memory (id, kind, text, source, created_at, confidence)
  select (r->>'id')::integer, r->>'kind', r->>'text', r->>'source',
         coalesce((r->>'created_at')::timestamptz, now()),
         coalesce((r->>'confidence')::real, 1)
  from jsonb_array_elements(p_rows) r
  on conflict (id) do update
    set kind = excluded.kind, text = excluded.text, source = excluded.source,
        created_at = excluded.created_at, confidence = excluded.confidence;
  select count(*) into n from public.celia_memory;
  return n;
end;
$$;

revoke all on function public.celia_relay_fetch(bigint, integer) from public, anon, authenticated;
revoke all on function public.celia_memory_replace(jsonb) from public, anon, authenticated;
grant execute on function public.celia_relay_fetch(bigint, integer) to service_role;
grant execute on function public.celia_memory_replace(jsonb) to service_role;
