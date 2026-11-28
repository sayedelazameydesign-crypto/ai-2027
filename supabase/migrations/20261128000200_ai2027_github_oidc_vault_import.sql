-- AI-2027: import allowlisted webhook secrets from GitHub Actions into Supabase Vault.
--
-- GitHub Actions secrets are write-only: only a workflow run can read them. The
-- run (.github/workflows/supabase-secret-sync.yml on main, workflow_dispatch)
-- authenticates to the edge function with a GitHub-signed OIDC token; the edge
-- function verifies it and calls ai2027_import_secrets with service_role.
--
--   * values live only in vault.secrets (encrypted at rest), names 'ai2027/<NAME>'
--   * the token's jti is burned in the same transaction -> no replay
--   * only four names are accepted; anything else aborts the whole import
--   * callers get back sha256 digests, never values
--   * both functions: SECURITY DEFINER, empty search_path, service_role only

create table if not exists public.ai2027_oidc_jti (
  jti     text primary key check (length(jti) between 8 and 256),
  exp     bigint not null,
  used_at timestamptz not null default now()
);
alter table public.ai2027_oidc_jti enable row level security;
revoke all on public.ai2027_oidc_jti from public, anon, authenticated;

create or replace function public.ai2027_import_secrets(p_jti text, p_exp bigint, p_items jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  allowed constant text[] := array[
    'YCLOUD_WEBHOOK_SECRET', 'WHATSAPP_CLOUD_APP_SECRET',
    'WHATSAPP_CLOUD_VERIFY_TOKEN', 'AI2027_WHATSAPP_WEBHOOK'];
  k text;
  v text;
  sid uuid;
  out jsonb := '[]'::jsonb;
begin
  if p_jti is null or length(p_jti) < 8 or p_items is null or jsonb_typeof(p_items) <> 'object'
     or p_items = '{}'::jsonb then
    raise exception 'bad_request';
  end if;
  delete from public.ai2027_oidc_jti where exp < extract(epoch from now())::bigint - 86400;
  insert into public.ai2027_oidc_jti (jti, exp) values (p_jti, p_exp) on conflict (jti) do nothing;
  if not found then
    return null; -- replayed token
  end if;
  for k, v in select key, value from jsonb_each_text(p_items) loop
    if not (k = any (allowed)) then
      raise exception 'name_not_allowed';  -- aborts the whole import incl. the jti burn
    end if;
    if v is null or length(v) < 8 or length(v) > 4096 then
      raise exception 'bad_value';
    end if;
    select id into sid from vault.secrets where name = 'ai2027/' || k;
    if sid is null then
      perform vault.create_secret(v, 'ai2027/' || k, 'imported from GitHub Actions via OIDC');
    else
      perform vault.update_secret(sid, v, 'ai2027/' || k, 'imported from GitHub Actions via OIDC');
    end if;
    out := out || jsonb_build_array(jsonb_build_object(
      'name', k, 'sha256', encode(extensions.digest(convert_to(v, 'UTF8'), 'sha256'), 'hex')));
  end loop;
  return out;
end
$$;

create or replace function public.ai2027_runtime_secrets()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_object_agg(substr(name, 8), decrypted_secret), '{}'::jsonb)
  from vault.decrypted_secrets
  where name in ('ai2027/YCLOUD_WEBHOOK_SECRET', 'ai2027/WHATSAPP_CLOUD_APP_SECRET',
                 'ai2027/WHATSAPP_CLOUD_VERIFY_TOKEN', 'ai2027/AI2027_WHATSAPP_WEBHOOK');
$$;

revoke all on function public.ai2027_import_secrets(text, bigint, jsonb) from public, anon, authenticated;
revoke all on function public.ai2027_runtime_secrets() from public, anon, authenticated;
grant execute on function public.ai2027_import_secrets(text, bigint, jsonb) to service_role;
grant execute on function public.ai2027_runtime_secrets() to service_role;
