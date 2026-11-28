-- CELIA: her edge secrets come from GitHub Actions through the EXISTING OIDC
-- import (20261128000200, .github/workflows/supabase-secret-sync.yml) — one
-- import door, no second credential, nobody pastes a key by hand.
--
--   * ai2027_import_secrets: allowlist grows by CELIA's five names (same
--     checks: jti burned atomically, any other name aborts the whole import,
--     8..4096 chars, digests returned — never values).
--   * ai2027_runtime_secrets: UNCHANGED — ycloud-webhook still reads only its
--     own four names.
--   * celia_runtime_secrets: new, returns ONLY CELIA's five names, for the
--     celia-telegram function (service_role only).
-- The gates CELIA_WEBHOOK / CELIA_CHAT are never importable: they stay plain
-- Edge env values that a human sets.

create or replace function public.ai2027_import_secrets(p_jti text, p_exp bigint, p_items jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  allowed constant text[] := array[
    'YCLOUD_WEBHOOK_SECRET', 'WHATSAPP_CLOUD_APP_SECRET',
    'WHATSAPP_CLOUD_VERIFY_TOKEN', 'AI2027_WHATSAPP_WEBHOOK',
    'CELIA_TELEGRAM_TOKEN', 'GEMINI_API_KEY', 'GROQ_API_KEY',
    'NVIDIA_API_KEY', 'OPENROUTER_API_KEY'];
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

create or replace function public.celia_runtime_secrets()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(jsonb_object_agg(substr(name, 8), decrypted_secret), '{}'::jsonb)
  from vault.decrypted_secrets
  where name in ('ai2027/CELIA_TELEGRAM_TOKEN', 'ai2027/GEMINI_API_KEY', 'ai2027/GROQ_API_KEY',
                 'ai2027/NVIDIA_API_KEY', 'ai2027/OPENROUTER_API_KEY');
$$;

revoke all on function public.ai2027_import_secrets(text, bigint, jsonb) from public, anon, authenticated;
revoke all on function public.celia_runtime_secrets() from public, anon, authenticated;
grant execute on function public.ai2027_import_secrets(text, bigint, jsonb) to service_role;
grant execute on function public.celia_runtime_secrets() to service_role;
