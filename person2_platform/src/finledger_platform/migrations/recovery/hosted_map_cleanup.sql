-- Delete only the fixture's fixed, marked firm and its two fixed clients.
begin;
do $$
begin
 if not exists(select 1 from firms where id='00000000-0000-4000-8000-00000000fa10' and name='zz-map-proof')
    or (select count(*) from clients where firm_id='00000000-0000-4000-8000-00000000fa10') <> 2
    or exists(select 1 from clients where firm_id='00000000-0000-4000-8000-00000000fa10' and id not in ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000cb10'))
    or exists(select 1 from users where firm_id='00000000-0000-4000-8000-00000000fa10' and id <> '00000000-0000-4000-8000-00000000aa10' and not (automation and email=automation_maker_email(firm_id)))
    or exists(select 1 from documents where client_id in ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000cb10') and id not in ('00000000-0000-4000-8000-00000000d010','00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000db10')) then
   raise exception 'unexpected fixture state; manual review required';
 end if;
end $$;
delete from documents where id in ('00000000-0000-4000-8000-00000000d010','00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000db10') and client_id in ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000cb10');
delete from clients where id in ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000cb10') and firm_id='00000000-0000-4000-8000-00000000fa10';
delete from users where firm_id='00000000-0000-4000-8000-00000000fa10' and (id='00000000-0000-4000-8000-00000000aa10' or (automation and email=automation_maker_email(firm_id)));
delete from firms where id='00000000-0000-4000-8000-00000000fa10' and name='zz-map-proof';
commit;
