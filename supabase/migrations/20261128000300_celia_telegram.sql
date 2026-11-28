-- CELIA on Telegram (Edge Function `celia-telegram`): consented memory,
-- short conversation history and webhook de-duplication.
--
--   * celia_memory   — ONLY what the owner explicitly asked CELIA to remember
--                      («تذكّري: …»); mirrors python-2027 MemoryRecord.
--   * celia_turns    — the last messages per chat (context for the free model).
--   * celia_updates  — Telegram update_ids already handled (retries are no-ops).
--
-- Same posture as 20261128000000_ai2027_free_stack.sql: RLS on with no
-- policies, everything revoked from PUBLIC/anon/authenticated; only the
-- server-side service_role (inside the Edge Function) can read or write.

create table if not exists public.celia_memory (
  id          integer primary key check (id >= 1),
  kind        text not null check (kind in ('personal', 'project', 'procedural', 'semantic')),
  text        text not null check (char_length(text) between 1 and 500),
  source      text not null check (char_length(source) between 1 and 120),
  created_at  timestamptz not null default now(),
  confidence  real not null default 1 check (confidence between 0 and 1)
);

create table if not exists public.celia_turns (
  id          bigint generated always as identity primary key,
  chat_id     text not null check (char_length(chat_id) between 1 and 64),
  role        text not null check (role in ('user', 'assistant')),
  content     text not null check (char_length(content) between 1 and 8000),
  created_at  timestamptz not null default now()
);
create index if not exists celia_turns_chat_id_idx on public.celia_turns (chat_id, id desc);

create table if not exists public.celia_updates (
  update_id   bigint primary key,
  received_at timestamptz not null default now()
);

alter table public.celia_memory  enable row level security;
alter table public.celia_turns   enable row level security;
alter table public.celia_updates enable row level security;

revoke all on public.celia_memory, public.celia_turns, public.celia_updates
  from public, anon, authenticated;
grant select, insert, update, delete on public.celia_memory, public.celia_turns,
  public.celia_updates to service_role;

-- First delivery of an update_id returns true; Telegram retries return false.
create or replace function public.celia_claim_update(p_update_id bigint)
returns boolean
language plpgsql security definer set search_path = public as $$
declare
  inserted integer;
begin
  insert into public.celia_updates (update_id) values (p_update_id)
  on conflict (update_id) do nothing;
  get diagnostics inserted = row_count;
  delete from public.celia_updates where received_at < now() - interval '7 days';
  return inserted = 1;
end;
$$;

-- One exchange (owner message + CELIA's reply), keeping the newest p_keep rows.
create or replace function public.celia_append_turns(
  p_chat_id text, p_user text, p_assistant text, p_keep integer default 40)
returns void
language plpgsql security definer set search_path = public as $$
begin
  insert into public.celia_turns (chat_id, role, content)
  values (p_chat_id, 'user', left(p_user, 8000)),
         (p_chat_id, 'assistant', left(p_assistant, 8000));
  delete from public.celia_turns
  where chat_id = p_chat_id
    and id not in (select id from public.celia_turns where chat_id = p_chat_id
                   order by id desc limit greatest(p_keep, 2));
end;
$$;

revoke all on function public.celia_claim_update(bigint) from public, anon, authenticated;
revoke all on function public.celia_append_turns(text, text, text, integer)
  from public, anon, authenticated;
grant execute on function public.celia_claim_update(bigint) to service_role;
grant execute on function public.celia_append_turns(text, text, text, integer) to service_role;
