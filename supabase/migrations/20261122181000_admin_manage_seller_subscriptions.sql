create or replace function public.zarinmah_admin_manage_seller_subscription(p_subscription_id bigint,p_action text,p_days integer default 30)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,auth,private as $$
declare v_uid uuid:=auth.uid(); v_sub public.seller_subscriptions%rowtype;
begin
  if v_uid is null or not private.zarinmah_is_admin() then raise exception 'فقط مدیرکل می‌تواند اشتراک فروشندگان را مدیریت کند.'; end if;
  if p_action not in ('extend','cancel') then raise exception 'عملیات اشتراک معتبر نیست.'; end if;
  select * into v_sub from public.seller_subscriptions where id=p_subscription_id for update;
  if not found then raise exception 'اشتراک پیدا نشد.'; end if;
  if p_action='cancel' then
    update public.seller_subscriptions set status='cancelled' where id=p_subscription_id returning * into v_sub;
  else
    if p_days is null or p_days<1 or p_days>365 then raise exception 'مدت تمدید باید بین ۱ تا ۳۶۵ روز باشد.'; end if;
    update public.seller_subscriptions set status='active',ends_at=greatest(ends_at,now())+make_interval(days=>p_days)
      where id=p_subscription_id returning * into v_sub;
  end if;
  return to_jsonb(v_sub);
end;
$$;
revoke all on function public.zarinmah_admin_manage_seller_subscription(bigint,text,integer) from public,anon;
grant execute on function public.zarinmah_admin_manage_seller_subscription(bigint,text,integer) to authenticated;