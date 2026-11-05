-- Synthetic operator-only fixture; do not use the application migrator.
begin;
insert into firms(id,name) values ('00000000-0000-4000-8000-00000000fa10','zz-map-proof');
insert into clients(id,firm_id,name,public_token,inbound_slug,auto_post_cap,maker_checker) values
 ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000fa10','zz-map-a',gen_random_uuid()::text,'zz-a-'||replace(gen_random_uuid()::text,'-',''),0,true),
 ('00000000-0000-4000-8000-00000000cb10','00000000-0000-4000-8000-00000000fa10','zz-map-b',gen_random_uuid()::text,'zz-b-'||replace(gen_random_uuid()::text,'-',''),0,true);
insert into users(id,firm_id,email,name) values
 ('00000000-0000-4000-8000-00000000aa10','00000000-0000-4000-8000-00000000fa10','zz-map-proof@example.invalid','zz-map-proof');
insert into documents(id,client_id,channel,object_path,content_hash,mime,size_bytes,status,virus_ok) values
 ('00000000-0000-4000-8000-00000000d010','00000000-0000-4000-8000-00000000ca10','portal','00000000-0000-4000-8000-00000000ca10/synthetic-old.pdf',repeat('c',64),'application/pdf',1,'extracted',true),
 ('00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000ca10','portal','00000000-0000-4000-8000-00000000ca10/synthetic-a.pdf',repeat('a',64),'application/pdf',1,'scored',true),
 ('00000000-0000-4000-8000-00000000db10','00000000-0000-4000-8000-00000000cb10','portal','00000000-0000-4000-8000-00000000cb10/synthetic-b.pdf',repeat('b',64),'application/pdf',1,'scored',true);
insert into canonical_invoices(document_id,client_id,maker_id,payload)
select d.id,d.client_id,'00000000-0000-4000-8000-00000000aa10',
 jsonb_build_object('document_id',d.id,'document_type','purchase_invoice',
 'vendor',jsonb_build_object('name','Synthetic Vendor','gstin','27AAACS1234A1Z2'),
 'buyer_gstin','27AAPFU0939F1ZV','invoice_no','ZZ-'||d.id::text,'invoice_date','2026-10-01',
 'taxable',10000,'cgst',900,'sgst',900,'igst',0,'cess',0,'round_off',0,'total',11800,
 'line_items',jsonb_build_array(jsonb_build_object('desc','synthetic widget calibration','hsn_sac','9983',
 'qty',1,'rate',10000,'taxable',10000,'tax_rate',18,'tax_amount',1800)),
 'confidences',jsonb_build_object('invoice_no',0.99,'date',0.99,'gstin',0.99,'total',0.99),
 'raw_markdown_path','synthetic','page_image_paths','[]'::jsonb)
from documents d where d.id in ('00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000db10');
insert into risk_assessments(client_id,document_id,score,band,marks)
select client_id,id,50,'medium','[]'::jsonb from documents
where id in ('00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000db10');
insert into correction_events(client_id,document_id,vendor_gstin,field,old_value,new_value,hsn,description,actor_id)
values ('00000000-0000-4000-8000-00000000ca10','00000000-0000-4000-8000-00000000d010',
 '27AAACS1234A1Z2','party_ledger','"Old synthetic ledger"'::jsonb,'"Corrected synthetic ledger"'::jsonb,
 '9983','synthetic widget calibration','00000000-0000-4000-8000-00000000aa10');
insert into jobs(client_id,document_id,queue,payload,state,attempts,claim_token,locked_until)
select client_id,id,'map',jsonb_build_object('client_id',client_id,'document_id',id),'running',1,
 case when id='00000000-0000-4000-8000-00000000da10' then '00000000-0000-4000-8000-00000000ea10'::uuid else '00000000-0000-4000-8000-00000000eb10'::uuid end,now()+interval '100 years'
from documents where id in ('00000000-0000-4000-8000-00000000da10','00000000-0000-4000-8000-00000000db10');
commit;
