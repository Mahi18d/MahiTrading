-- Run once in your OWN Supabase project's SQL Editor. No broker credentials here.
-- The API key used by Streamlit must be a server secret/service-role key.
create table if not exists public.mahi_paper_accounts (
  owner_id text primary key,
  revision bigint not null default 0,
  data jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.mahi_paper_accounts enable row level security;
revoke all on public.mahi_paper_accounts from public, anon, authenticated;
grant select, insert, update on public.mahi_paper_accounts to service_role;

create or replace function public.mahi_read_account(p_owner text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.mahi_paper_accounts;
begin
  if p_owner !~ '^[a-f0-9]{64}$' then raise exception 'Invalid owner'; end if;
  insert into public.mahi_paper_accounts(owner_id, data)
    values (p_owner, '{"cash":500000,"positions":[],"closed_trades":[]}'::jsonb)
    on conflict (owner_id) do nothing;
  select * into strict r from public.mahi_paper_accounts where owner_id = p_owner;
  return jsonb_build_object('data',r.data,'revision',r.revision,'updated_at',r.updated_at);
end;
$$;

create or replace function public.mahi_save_account(p_owner text, p_expected_revision bigint, p_data jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.mahi_paper_accounts;
begin
  if p_owner !~ '^[a-f0-9]{64}$' or jsonb_typeof(p_data) <> 'object'
    or jsonb_typeof(p_data->'cash') is distinct from 'number'
    or jsonb_typeof(p_data->'positions') is distinct from 'array'
    or jsonb_typeof(p_data->'closed_trades') is distinct from 'array'
    then raise exception 'Invalid account'; end if;
  update public.mahi_paper_accounts
    set data = p_data, revision = revision + 1, updated_at = now()
    where owner_id = p_owner and revision = p_expected_revision
    returning * into r;
  if not found then return jsonb_build_object('conflict',true); end if;
  return jsonb_build_object('revision',r.revision,'updated_at',r.updated_at);
end;
$$;
revoke execute on function public.mahi_read_account(text) from public, anon, authenticated;
revoke execute on function public.mahi_save_account(text,bigint,jsonb) from public, anon, authenticated;
grant execute on function public.mahi_read_account(text) to service_role;
grant execute on function public.mahi_save_account(text,bigint,jsonb) to service_role;
