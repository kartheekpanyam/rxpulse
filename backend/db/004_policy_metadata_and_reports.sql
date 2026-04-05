alter table public.documents
add column if not exists title text;

alter table public.drug_coverages
add column if not exists generic_name text,
add column if not exists family_name text,
add column if not exists product_name text,
add column if not exists product_key text,
add column if not exists policy_name text,
add column if not exists document_type text,
add column if not exists coverage_bucket text,
add column if not exists source_pages jsonb not null default '[]'::jsonb,
add column if not exists source_section text,
add column if not exists evidence_snippet text;

create index if not exists idx_documents_title
on public.documents(title);

create index if not exists idx_drug_coverages_generic_name
on public.drug_coverages(generic_name);

create index if not exists idx_drug_coverages_family_name
on public.drug_coverages(family_name);

create index if not exists idx_drug_coverages_product_key
on public.drug_coverages(product_key);

create index if not exists idx_drug_coverages_policy_name
on public.drug_coverages(policy_name);
