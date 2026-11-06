-- Historical proof before migration 016; fl_map no longer has UPDATE on jobs.
update jobs set state='queued', claim_token=null, locked_until=null, run_after='2000-01-01 UTC'
where client_id='00000000-0000-4000-8000-00000000ca10'
  and document_id='00000000-0000-4000-8000-00000000da10' and queue='map' and state='running'
returning id;
