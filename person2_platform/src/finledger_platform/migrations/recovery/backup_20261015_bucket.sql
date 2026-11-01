-- The backup's only non-history configuration row: a private, empty bucket.
-- Do not overwrite an existing destination bucket with different settings.
do $$
begin
  if exists (select 1 from storage.buckets
             where id = 'finledger-documents-dev'
               and (name is distinct from 'finledger-documents-dev'
                    or public is distinct from false
                    or file_size_limit is distinct from 26214400
                    or allowed_mime_types is not null)) then
    raise exception 'destination bucket settings differ; inspect before restoring';
  end if;
  insert into storage.buckets (id, name, public, file_size_limit)
  values ('finledger-documents-dev', 'finledger-documents-dev', false, 26214400)
  on conflict (id) do nothing;
end $$;
