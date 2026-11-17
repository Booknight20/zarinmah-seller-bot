-- Fix PostgREST permissions for the backend service-role client.
-- Apply only to the isolated Staging Supabase project.
grant select, insert, update on table public.user_notifications to service_role;
grant usage, select on sequence public.user_notifications_id_seq to service_role;
