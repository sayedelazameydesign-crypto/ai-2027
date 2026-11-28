-- Applied to project ziaedqqhvsmcwcmlbcpi after 20261128000000.
--  * honest source label: the first tick (row id 1) was run MANUALLY from the SQL
--    console but labelled 'pg_cron' by the original function body; rows are
--    append-only so it stays, and this note is its correction. From here on the
--    label is derived from application_name.
--  * public probe target + 5-minute schedule. The URL is not a secret.
-- (The full function body lives in 20261128000000_ai2027_free_stack.sql.)
insert into public.ai2027_config (key, value)
values ('healthz_url', 'https://ziaedqqhvsmcwcmlbcpi.supabase.co/functions/v1/ycloud-webhook/healthz')
on conflict (key) do update set value = excluded.value;

select cron.schedule('ai2027-watchdog', '*/5 * * * *', $$select public.ai2027_watchdog_tick()$$);
