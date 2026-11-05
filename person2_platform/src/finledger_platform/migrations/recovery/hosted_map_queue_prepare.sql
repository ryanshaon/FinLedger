-- Synthetic fixture only. Execute as fl_map inside a transaction that is always rolled back.
update jobs set state='queued', claim_token=null, locked_until=null, run_after='2000-01-01 UTC'
where client_id='00000000-0000-4000-8000-00000000ca10'
  and document_id='00000000-0000-4000-8000-00000000da10' and queue='map' and state='running'
returning id;
