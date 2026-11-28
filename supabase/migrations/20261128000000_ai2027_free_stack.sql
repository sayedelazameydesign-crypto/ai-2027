-- AI-2027 free 24/7 stack on Supabase Free (no card).
--
-- Postgres port of the SQLite schema in scripts/free_stack_runner.py plus a
-- tamper-evident, hash-linked watchdog log that pg_cron appends to every 5 min.
--
-- Security model:
--   * every table has RLS enabled and NO policies -> anon/authenticated read nothing;
--   * every function is revoked from PUBLIC/anon/authenticated and granted only to
--     service_role (the Edge Function's server-side key);
--   * watchdog_runs is append-only (UPDATE/DELETE/TRUNCATE raise) and every row's
--     hash covers the previous row's hash, so an edit made by disabling the
--     trigger is still detected by ai2027_verify_watchdog_chain().

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- ---------------------------------------------------------------- CRM tables
create table if not exists public.inbox_messages (
    wamid               text primary key,
    sender              text    not null,
    phone_number_id     text    not null,
    body                text    not null,
    msg_ts              bigint  not null,
    status              text    not null,
    reply_text          text    not null default '',
    payload_hash        text    not null default '',
    outbound_message_id text    not null default '',
    created_at          bigint  not null
);

create table if not exists public.coupons (
    code         text primary key,
    customer_id  text    not null,
    discount_pct integer not null check (discount_pct between 1 and 100),
    used         integer not null default 0,
    expires_at   bigint  not null
);

create table if not exists public.verification_challenges (
    customer_id text primary key,
    salt_hex    text    not null,
    derived_hex text    not null,
    attempts    integer not null default 0,
    expires_at  bigint  not null
);

create table if not exists public.daily_quota (
    day_key          text primary key,
    prepared_count   integer not null default 0,
    dispatched_count integer not null default 0
);

-- ---------------------------------------------------------- watchdog tables
create table if not exists public.ai2027_config (
    key   text primary key,
    value text not null
);

create table if not exists public.watchdog_runs (
    id        bigint generated always as identity primary key,
    ran_at    timestamptz not null default now(),
    source    text        not null,
    checks    jsonb       not null,
    executed  integer     not null,
    passed    integer     not null,
    total     integer     not null,
    verdict   text        not null check (verdict in ('pass', 'degraded', 'fail')),
    prev_hash text        not null,
    row_hash  text        not null unique
);

create table if not exists public.watchdog_pending (
    request_id bigint primary key,
    fired_at   timestamptz not null default now()
);

alter table public.inbox_messages          enable row level security;
alter table public.coupons                 enable row level security;
alter table public.verification_challenges enable row level security;
alter table public.daily_quota             enable row level security;
alter table public.ai2027_config           enable row level security;
alter table public.watchdog_runs           enable row level security;
alter table public.watchdog_pending        enable row level security;

revoke all on public.inbox_messages, public.coupons, public.verification_challenges,
              public.daily_quota, public.ai2027_config, public.watchdog_runs,
              public.watchdog_pending
    from anon, authenticated;

-- ------------------------------------------------------- append-only guard
create or replace function public.ai2027_forbid_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'watchdog_runs is append-only (% refused)', tg_op;
end;
$$;

drop trigger if exists watchdog_runs_append_only on public.watchdog_runs;
create trigger watchdog_runs_append_only
    before update or delete on public.watchdog_runs
    for each row execute function public.ai2027_forbid_mutation();

drop trigger if exists watchdog_runs_no_truncate on public.watchdog_runs;
create trigger watchdog_runs_no_truncate
    before truncate on public.watchdog_runs
    for each statement execute function public.ai2027_forbid_mutation();

-- ------------------------------------------------------------- hash helpers
create or replace function public.ai2027_watchdog_row_hash(
    p_prev_hash text, p_ran_at timestamptz, p_source text, p_checks jsonb, p_verdict text
) returns text language sql immutable as $$
    select encode(
        sha256(convert_to(
            p_prev_hash || '|' ||
            to_char(p_ran_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') || '|' ||
            p_source || '|' || p_checks::text || '|' || p_verdict,
            'UTF8')),
        'hex');
$$;

-- Append one run. Checks are [{"name":..,"status":"pass"|"fail"|"skip","detail":..}].
-- verdict = pass only if EVERY check executed and passed; any fail -> fail;
-- otherwise (some skipped) -> degraded. A skip is never counted as a pass.
create or replace function public.ai2027_append_watchdog_run(p_source text, p_checks jsonb)
returns public.watchdog_runs
language plpgsql security definer set search_path = public as $$
declare
    v_total    integer;
    v_executed integer;
    v_passed   integer;
    v_verdict  text;
    v_prev     text;
    v_ran_at   timestamptz := clock_timestamp();
    v_row      public.watchdog_runs;
begin
    if jsonb_typeof(p_checks) <> 'array' or jsonb_array_length(p_checks) = 0 then
        raise exception 'checks must be a non-empty JSON array';
    end if;
    if exists (
        select 1 from jsonb_array_elements(p_checks) c
        where coalesce(c->>'status', '') not in ('pass', 'fail', 'skip')
           or coalesce(c->>'name', '') = ''
    ) then
        raise exception 'every check needs a name and status pass|fail|skip';
    end if;

    perform pg_advisory_xact_lock(hashtext('ai2027_watchdog_chain'));

    select count(*),
           count(*) filter (where c->>'status' in ('pass', 'fail')),
           count(*) filter (where c->>'status' = 'pass')
      into v_total, v_executed, v_passed
      from jsonb_array_elements(p_checks) c;

    v_verdict := case
        when v_passed = v_total then 'pass'
        when v_executed > v_passed then 'fail'
        else 'degraded'
    end;

    select row_hash into v_prev from public.watchdog_runs order by id desc limit 1;
    v_prev := coalesce(v_prev, repeat('0', 64));

    insert into public.watchdog_runs
        (ran_at, source, checks, executed, passed, total, verdict, prev_hash, row_hash)
    values
        (v_ran_at, p_source, p_checks, v_executed, v_passed, v_total, v_verdict, v_prev,
         public.ai2027_watchdog_row_hash(v_prev, v_ran_at, p_source, p_checks, v_verdict))
    returning * into v_row;
    return v_row;
end;
$$;

-- Recompute the whole chain. ok=false names the first row that does not verify.
create or replace function public.ai2027_verify_watchdog_chain(
    out rows_checked bigint, out ok boolean, out first_bad_id bigint
)
language plpgsql stable security definer set search_path = public as $$
declare
    r      record;
    v_prev text := repeat('0', 64);
begin
    rows_checked := 0;
    ok := true;
    first_bad_id := null;
    for r in select * from public.watchdog_runs order by id loop
        rows_checked := rows_checked + 1;
        if r.prev_hash <> v_prev
           or r.row_hash <> public.ai2027_watchdog_row_hash(
                  r.prev_hash, r.ran_at, r.source, r.checks, r.verdict) then
            ok := false;
            first_bad_id := r.id;
            return;
        end if;
        v_prev := r.row_hash;
    end loop;
end;
$$;

-- ------------------------------------------------------ Edge Function RPCs
create or replace function public.ai2027_healthz()
returns boolean language sql stable security definer set search_path = public as $$
    -- Reads a real table: proves schema present + DB answering (never a constant).
    select count(*) >= 0 from public.daily_quota;
$$;

-- Atomic port of ZeroCostOrchestrator.ingest_event's gate: duplicate check,
-- blocked-stage record, daily-quota check and quota reservation in ONE
-- transaction under an advisory lock (no double-spend under concurrency).
create or replace function public.ai2027_stage_inbound(
    p_wamid text, p_sender text, p_phone_number_id text, p_body text,
    p_msg_ts bigint, p_stage text, p_cap integer, p_now bigint
) returns text
language plpgsql security definer set search_path = public as $$
declare
    v_day  text := to_char(to_timestamp(p_now) at time zone 'UTC', 'YYYY-MM-DD');
    v_cap  integer := greatest(coalesce(p_cap, 0), 0);
    v_used integer;
    v_reason text;
begin
    perform pg_advisory_xact_lock(hashtext('ai2027_daily_quota'));

    if p_stage = 'duplicate_event'
       or exists (select 1 from public.inbox_messages where wamid = p_wamid) then
        return 'duplicate_event';
    end if;

    if p_stage <> 'accepted_text' then
        insert into public.inbox_messages
            (wamid, sender, phone_number_id, body, msg_ts, status, created_at)
        values (p_wamid, p_sender, p_phone_number_id, p_body, p_msg_ts, 'blocked:' || p_stage, p_now);
        return p_stage;
    end if;

    select greatest(prepared_count, dispatched_count) into v_used
      from public.daily_quota where day_key = v_day;
    v_used := coalesce(v_used, 0);
    if v_used >= v_cap then
        v_reason := 'daily_free_quota_exceeded:' || v_used || '>=' || v_cap;
        insert into public.inbox_messages
            (wamid, sender, phone_number_id, body, msg_ts, status, created_at)
        values (p_wamid, p_sender, p_phone_number_id, p_body, p_msg_ts, 'blocked:' || v_reason, p_now);
        return v_reason;
    end if;

    insert into public.inbox_messages
        (wamid, sender, phone_number_id, body, msg_ts, status, created_at)
    values (p_wamid, p_sender, p_phone_number_id, p_body, p_msg_ts, 'reserved', p_now);
    insert into public.daily_quota (day_key, prepared_count, dispatched_count)
    values (v_day, 1, 0)
    on conflict (day_key) do update set prepared_count = public.daily_quota.prepared_count + 1;
    return 'reserved';
end;
$$;

create or replace function public.ai2027_finalize_reply(
    p_wamid text, p_reply_text text, p_payload_hash text
) returns boolean
language plpgsql security definer set search_path = public as $$
begin
    if p_payload_hash !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_reply_text), '') = '' then
        raise exception 'finalize_reply needs a non-empty reply and a sha256 hex hash';
    end if;
    update public.inbox_messages
       set status = 'pending_approval', reply_text = p_reply_text, payload_hash = p_payload_hash
     where wamid = p_wamid and status = 'reserved';
    return found;
end;
$$;

create or replace function public.ai2027_issue_coupon(
    p_code text, p_customer_id text, p_discount_pct integer, p_expires_at bigint
) returns void
language sql security definer set search_path = public as $$
    insert into public.coupons (code, customer_id, discount_pct, used, expires_at)
    values (p_code, p_customer_id, greatest(1, least(100, p_discount_pct)), 0, p_expires_at);
$$;

create or replace function public.ai2027_put_verification(
    p_customer_id text, p_salt_hex text, p_derived_hex text, p_expires_at bigint
) returns void
language sql security definer set search_path = public as $$
    insert into public.verification_challenges (customer_id, salt_hex, derived_hex, attempts, expires_at)
    values (p_customer_id, p_salt_hex, p_derived_hex, 0, p_expires_at)
    on conflict (customer_id) do update set
        salt_hex = excluded.salt_hex, derived_hex = excluded.derived_hex,
        attempts = 0, expires_at = excluded.expires_at;
$$;

-- Public, PII-free status: last run + chain verification. No counts of messages.
create or replace function public.ai2027_public_status()
returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
    v_last  public.watchdog_runs;
    v_chain record;
    v_day   jsonb;
begin
    select * into v_last from public.watchdog_runs order by id desc limit 1;
    select * into v_chain from public.ai2027_verify_watchdog_chain();
    select jsonb_build_object(
               'runs', count(*),
               'pass', count(*) filter (where verdict = 'pass'),
               'degraded', count(*) filter (where verdict = 'degraded'),
               'fail', count(*) filter (where verdict = 'fail'))
      into v_day
      from public.watchdog_runs where ran_at > now() - interval '24 hours';
    return jsonb_build_object(
        'service', 'free-stack-webhook',
        'chain', jsonb_build_object('ok', v_chain.ok, 'rows_checked', v_chain.rows_checked,
                                    'first_bad_id', v_chain.first_bad_id),
        'last_24h', v_day,
        'last_run', case when v_last.id is null then null else jsonb_build_object(
            'id', v_last.id, 'ran_at', v_last.ran_at, 'source', v_last.source,
            'verdict', v_last.verdict,
            'coverage', v_last.executed || ' of ' || v_last.total || ' executed',
            'passed', v_last.passed, 'checks', v_last.checks, 'row_hash', v_last.row_hash) end);
end;
$$;

-- Never lets a non-JSON probe body abort the tick (a silent tick is the worst failure).
create or replace function public.ai2027_body_ok(p_body text)
returns boolean language plpgsql immutable as $$
begin
    return coalesce((p_body::jsonb ->> 'ok')::boolean, false);
exception when others then
    return false;
end;
$$;

-- ------------------------------------------------------------ the 5-min tick
-- 1. cron_alive      : this function ran (pg_cron is scheduling).
-- 2. edge_healthz    : result of the HTTPS probe fired by the PREVIOUS tick
--                      (pg_net -> public URL -> Edge Function -> PostgREST -> DB).
--                      No response yet => skip (never pass).
-- 3. chain_verified  : full recomputation of the hash chain.
-- 4. inbox_integrity : no inbound row stuck in 'reserved' > 2 min and every
--                      pending_approval row carries a sha256 payload hash.
-- Then it fires the next probe.
create or replace function public.ai2027_watchdog_tick()
returns public.watchdog_runs
language plpgsql security definer set search_path = public, extensions as $$
declare
    v_checks  jsonb := '[]'::jsonb;
    v_url     text;
    v_pending record;
    v_resp    record;
    v_chain   record;
    v_stuck   integer;
    v_bad     integer;
    v_req     bigint;
    v_row     public.watchdog_runs;
begin
    v_checks := v_checks || jsonb_build_object('name', 'cron_alive', 'status', 'pass',
                                               'detail', 'pg_cron tick executed');

    select request_id, fired_at into v_pending
      from public.watchdog_pending order by fired_at desc limit 1;
    if v_pending.request_id is null then
        v_checks := v_checks || jsonb_build_object('name', 'edge_healthz', 'status', 'skip',
                                                   'detail', 'no probe fired yet (first tick)');
    else
        select status_code, content, timed_out, error_msg into v_resp
          from net._http_response where id = v_pending.request_id;
        if not found then
            v_checks := v_checks || jsonb_build_object('name', 'edge_healthz', 'status', 'skip',
                                                       'detail', 'probe response not recorded yet');
        elsif v_resp.status_code = 200 and public.ai2027_body_ok(v_resp.content) then
            v_checks := v_checks || jsonb_build_object('name', 'edge_healthz', 'status', 'pass',
                                                       'detail', 'HTTP 200 ok:true');
        else
            v_checks := v_checks || jsonb_build_object('name', 'edge_healthz', 'status', 'fail',
                'detail', coalesce('HTTP ' || v_resp.status_code::text,
                                   case when v_resp.timed_out then 'timed out' else 'error' end));
        end if;
    end if;

    select * into v_chain from public.ai2027_verify_watchdog_chain();
    v_checks := v_checks || jsonb_build_object(
        'name', 'chain_verified',
        'status', case when v_chain.ok then 'pass' else 'fail' end,
        'detail', case when v_chain.ok then v_chain.rows_checked || ' rows verified'
                       else 'first bad row id ' || v_chain.first_bad_id end);

    select count(*) into v_stuck from public.inbox_messages
     where status = 'reserved' and created_at < extract(epoch from now())::bigint - 120;
    select count(*) into v_bad from public.inbox_messages
     where status = 'pending_approval' and payload_hash !~ '^[0-9a-f]{64}$';
    v_checks := v_checks || jsonb_build_object(
        'name', 'inbox_integrity',
        'status', case when v_stuck = 0 and v_bad = 0 then 'pass' else 'fail' end,
        'detail', 'stuck_reserved=' || v_stuck || ' bad_hash=' || v_bad);

    -- Label from the session itself: pg_cron sets application_name='pg_cron'; any
    -- manual/SQL-editor call is recorded as 'manual' (never impersonates the cron).
    v_row := public.ai2027_append_watchdog_run(
        case when current_setting('application_name', true) = 'pg_cron'
             then 'pg_cron' else 'manual' end,
        v_checks);

    delete from public.watchdog_pending;
    select value into v_url from public.ai2027_config where key = 'healthz_url';
    if v_url is not null then
        select net.http_get(url := v_url, timeout_milliseconds := 10000) into v_req;
        insert into public.watchdog_pending (request_id) values (v_req);
    end if;
    return v_row;
end;
$$;

-- ------------------------------------------------------------------- grants
do $$
declare f text;
begin
    foreach f in array array[
        'public.ai2027_append_watchdog_run(text, jsonb)',
        'public.ai2027_verify_watchdog_chain()',
        'public.ai2027_healthz()',
        'public.ai2027_stage_inbound(text, text, text, text, bigint, text, integer, bigint)',
        'public.ai2027_finalize_reply(text, text, text)',
        'public.ai2027_issue_coupon(text, text, integer, bigint)',
        'public.ai2027_put_verification(text, text, text, bigint)',
        'public.ai2027_public_status()',
        'public.ai2027_watchdog_tick()',
        'public.ai2027_watchdog_row_hash(text, timestamptz, text, jsonb, text)',
        'public.ai2027_forbid_mutation()',
        'public.ai2027_body_ok(text)'
    ] loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to service_role', f);
    end loop;
end;
$$;
