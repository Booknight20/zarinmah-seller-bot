-- Optional seller-provided rejection reason for purchase requests.
-- Apply only to the isolated Staging Supabase project.

alter table public.purchase_requests
  add column if not exists rejection_reason_code text,
  add column if not exists rejection_reason_label text,
  add column if not exists rejection_reason_text text;

create or replace function public.create_purchase_request_notifications()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  seller_id uuid;
  listing_label text;
  rejection_message text;
begin
  select l.owner_user_id,
         coalesce(nullif(l.title, ''), nullif(l.product_type, ''), 'آگهی')
    into seller_id, listing_label
    from public.listings l
   where l.id = new.listing_id;

  if tg_op = 'INSERT' then
    if seller_id is not null then
      insert into public.user_notifications
        (recipient_user_id, event_key, notification_type, title, message, request_id)
      values
        (seller_id, 'purchase-request:new:' || new.id::text,
         'purchase_request_new', 'درخواست خرید جدید',
         'برای آگهی «' || listing_label || '» درخواست خرید ثبت شده است.', new.id)
      on conflict (event_key) do nothing;
    end if;
    return new;
  end if;

  if new.status is distinct from old.status then
    if new.status = 'approved' and new.buyer_user_id is not null then
      insert into public.user_notifications
        (recipient_user_id, event_key, notification_type, title, message, request_id)
      values
        (new.buyer_user_id, 'purchase-request:approved:' || new.id::text,
         'purchase_request_approved', 'درخواست خرید تأیید شد',
         'فروشنده درخواست شما برای آگهی «' || listing_label || '» را تأیید کرده است. مهلت پرداخت در پنل شما نمایش داده می‌شود.', new.id)
      on conflict (event_key) do nothing;
    elsif new.status = 'rejected' and new.buyer_user_id is not null then
      rejection_message := 'فروشنده درخواست شما برای آگهی «' || listing_label || '» را نپذیرفته است.';
      if nullif(trim(new.rejection_reason_label), '') is not null then
        rejection_message := rejection_message || ' علت: ' || trim(new.rejection_reason_label) || '.';
      end if;
      if nullif(trim(new.rejection_reason_text), '') is not null then
        rejection_message := rejection_message || ' توضیح فروشنده: ' || trim(new.rejection_reason_text);
      end if;
      insert into public.user_notifications
        (recipient_user_id, event_key, notification_type, title, message, request_id)
      values
        (new.buyer_user_id, 'purchase-request:rejected:' || new.id::text,
         'purchase_request_rejected', 'درخواست خرید رد شد',
         rejection_message, new.id)
      on conflict (event_key) do nothing;
    elsif new.status = 'expired' and new.buyer_user_id is not null then
      insert into public.user_notifications
        (recipient_user_id, event_key, notification_type, title, message, request_id)
      values
        (new.buyer_user_id, 'purchase-request:expired:' || new.id::text,
         'purchase_request_expired', 'مهلت پرداخت به پایان رسید',
         'مهلت پرداخت درخواست آگهی «' || listing_label || '» به پایان رسیده است.', new.id)
      on conflict (event_key) do nothing;
    elsif new.status = 'paid' then
      if new.buyer_user_id is not null then
        insert into public.user_notifications
          (recipient_user_id, event_key, notification_type, title, message, request_id)
        values
          (new.buyer_user_id, 'purchase-request:paid:buyer:' || new.id::text,
           'purchase_payment_paid', 'پرداخت با موفقیت تأیید شد',
           'پرداخت درخواست آگهی «' || listing_label || '» تأیید شده است.', new.id)
        on conflict (event_key) do nothing;
      end if;
      if seller_id is not null then
        insert into public.user_notifications
          (recipient_user_id, event_key, notification_type, title, message, request_id)
        values
          (seller_id, 'purchase-request:paid:seller:' || new.id::text,
           'purchase_payment_paid', 'پرداخت خریدار تأیید شد',
           'پرداخت دسترسی به اطلاعات تماس برای آگهی «' || listing_label || '» تأیید شده است.', new.id)
        on conflict (event_key) do nothing;
      end if;
    end if;
  end if;

  return new;
end;
$$;