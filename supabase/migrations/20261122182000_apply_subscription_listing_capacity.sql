create or replace function public.zarinmah_create_seller_listing(p_listing jsonb)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,public,auth,storage
as $$
declare
  v_user_id uuid:=auth.uid(); v_tariff jsonb; v_payment jsonb;
  v_free_quota integer; v_fee bigint; v_duration integer;
  v_listing_count bigint; v_paid_required boolean; v_package_limit integer:=0;
  v_package_active_count bigint:=0; v_covered_by_package boolean:=false;
  v_listing public.listings%rowtype; v_order public.seller_payment_orders%rowtype;
  v_verified_phone text; v_verified_name text; v_image_path text; v_invoice_path text;
begin
  if v_user_id is null then raise exception 'برای ثبت آگهی ابتدا وارد حساب کاربری شوید.'; end if;
  if coalesce(p_listing->>'owner_user_id','')<>v_user_id::text then raise exception 'مالک آگهی با حساب واردشده مطابقت ندارد.'; end if;
  if coalesce(p_listing->>'status','pending')<>'pending' then raise exception 'وضعیت اولیه آگهی معتبر نیست.'; end if;
  select phone into v_verified_phone from auth.users where id=v_user_id;
  select full_name into v_verified_name from public.profiles where id=v_user_id;
  if coalesce(v_verified_phone,'')='' or regexp_replace(v_verified_phone,'[^0-9]','','g')<>regexp_replace(coalesce(p_listing->>'seller_mobile',''),'[^0-9]','','g') then raise exception 'شماره تماس آگهی باید با شماره تأییدشده حساب یکسان باشد.'; end if;
  if coalesce(trim(v_verified_name),'')='' or trim(v_verified_name)<>trim(coalesce(p_listing->>'seller_name','')) then raise exception 'نام فروشنده باید از پروفایل تکمیل‌شده حساب دریافت شود.'; end if;
  v_image_path:=regexp_replace(coalesce(p_listing->>'image_url',''),'^.*/storage/v1/object/public/listing-images/','');
  v_invoice_path:=coalesce(p_listing->>'invoice_path','');
  if v_image_path='' or not exists(select 1 from storage.objects where bucket_id='listing-images' and name=v_image_path and owner_id=v_user_id::text) then raise exception 'تصویر آگهی متعلق به این حساب نیست یا بارگذاری نشده است.'; end if;
  if v_invoice_path='' or not exists(select 1 from storage.objects where bucket_id='listing-invoices' and name=v_invoice_path and owner_id=v_user_id::text) then raise exception 'فاکتور آگهی متعلق به این حساب نیست یا بارگذاری نشده است.'; end if;
  select coalesce(setting_value,'{}'::jsonb) into v_tariff from public.zarinmah_site_settings where setting_key='seller_tariff_config';
  select coalesce(setting_value,'{}'::jsonb) into v_payment from public.zarinmah_site_settings where setting_key='seller_payment_config';
  v_free_quota:=greatest(0,least(100,coalesce(nullif(v_tariff->>'free_listing_quota','')::integer,3)));
  v_fee:=greatest(0,coalesce(nullif(v_tariff->>'listing_price_toman','')::bigint,25000));
  v_duration:=greatest(1,least(365,coalesce(nullif(v_tariff->>'listing_duration_days','')::integer,30)));
  select listing_limit into v_package_limit from public.seller_subscriptions where user_id=v_user_id and status='active' and starts_at<=now() and ends_at>now() order by ends_at desc limit 1;
  if coalesce(v_package_limit,0)>0 then
    select count(*) into v_package_active_count from public.listings where owner_user_id=v_user_id and deleted_at is null and status in ('pending','published');
    v_covered_by_package:=v_package_active_count<v_package_limit;
  end if;
  select count(*) into v_listing_count from public.listings where owner_user_id=v_user_id and deleted_at is null and status in ('pending','published','sold');
  v_paid_required:=not v_covered_by_package and v_listing_count>=v_free_quota and v_fee>0;
  if v_paid_required and (coalesce((v_payment->>'enabled')::boolean,false) is not true or length(regexp_replace(coalesce(v_payment->>'card_number',''),'[^0-9]','','g'))<>16 or length(trim(coalesce(v_payment->>'card_owner','')))<3) then raise exception 'سهمیه آگهی رایگان یا ظرفیت اشتراک شما به پایان رسیده و دریافت پرداخت فعلاً از طرف مدیر غیرفعال است.'; end if;
  insert into public.listings(owner_user_id,owner_telegram_id,title,product_type,weight,purity,pricing_method,price,pricing_value,description,image_url,status,created_at,updated_at,seller_name,seller_mobile,weight_unit,province,city,invoice_path,payment_status)
  values(v_user_id,null,coalesce(nullif(p_listing->>'title',''),nullif(p_listing->>'product_type','')),p_listing->>'product_type',
    nullif(p_listing->>'weight','')::numeric,nullif(p_listing->>'purity',''),p_listing->>'pricing_method',nullif(p_listing->>'price','')::numeric,
    nullif(p_listing->>'pricing_value','')::numeric,p_listing->>'description',p_listing->>'image_url','pending',now(),now(),v_verified_name,v_verified_phone,
    p_listing->>'weight_unit',p_listing->>'province',p_listing->>'city',v_invoice_path,
    case when v_paid_required then 'awaiting_payment' when v_covered_by_package then 'package' else 'free' end) returning * into v_listing;
  if v_paid_required then
    insert into public.seller_payment_orders(user_id,purpose,listing_id,amount_toman,payment_method,provider,status,metadata)
    values(v_user_id,'listing_fee',v_listing.id,v_fee,'manual_card_transfer','manual_card_transfer','awaiting_receipt',jsonb_build_object('listing_title',v_listing.title,'display_duration_days',v_duration))
    returning * into v_order;
    update public.listings set payment_order_id=v_order.id,updated_at=now() where id=v_listing.id returning * into v_listing;
  end if;
  return jsonb_build_object('listing',to_jsonb(v_listing),'payment_required',v_paid_required,'package_covered',v_covered_by_package,
    'listing_duration_days',v_duration,'payment_order',case when v_paid_required then jsonb_build_object('id',v_order.id,'amount_toman',v_order.amount_toman,'purpose',v_order.purpose,'status',v_order.status) else null end);
end;
$$;
revoke all on function public.zarinmah_create_seller_listing(jsonb) from public,anon;
grant execute on function public.zarinmah_create_seller_listing(jsonb) to authenticated;